"""Independent in-cluster query streams for the replacement workload."""

import json
import os
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from sieve_tool.paths import repository_root
from sieve_tool.process import ManagedProcess, deadline
from sieve_tool.testing.child_repair import get

ROUTES = {"pooler": "sieve-pooler.default.svc", "direct": "sieve-db-rw.default.svc"}


class Clients:
    def __init__(self, directory: Path, stack: ExitStack, kubectl: Callable[..., str]):
        self.directory, self.stack, self.kubectl = directory, stack, kubectl
        self.followers: list[ManagedProcess] = []
        self.events: dict[str, Any] = {}

    def mark(self, name: str) -> None:
        self.events[name] = {
            "wall_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(),
        }

    def records(self, route: str) -> list[dict[str, Any]]:
        path = self.directory / ("client-" + route + ".jsonl")
        if not path.exists():
            return []
        # A follower may be writing its final line; completed lines must parse.
        return [
            json.loads(line)
            for line in path.read_text().splitlines(keepends=True)
            if line.endswith("\n")
        ]

    def start(self) -> None:
        script = Path(__file__).with_name("pooler_client.py").read_text()
        containers = []
        for route, host in ROUTES.items():
            containers.append(
                {
                    "name": route,
                    "image": os.environ.get(
                        "SIEVE_CNPG_POSTGRES_IMAGE",
                        "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie",
                    ),
                    "command": ["python3", "-u", "-c", script],
                    "args": ["--route", route, "--host", host],
                    # This is a public synthetic benchmark login, never a copied credential.
                    "env": [
                        {"name": "PGPASSWORD", "value": "sieve-public-probe-fixture"},
                        {"name": "PGCONNECT_TIMEOUT", "value": "2"},
                        {"name": "PGOPTIONS", "value": "-c statement_timeout=2000"},
                    ],
                    "resources": {"requests": {"memory": "32Mi", "cpu": "20m"}},
                }
            )
        self.kubectl(
            "create",
            "-f",
            "-",
            "-o",
            "json",
            payload={
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "sieve-client"},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": containers,
                },
            },
        )

    def follow(self) -> None:
        for route in ROUTES:
            self.followers.append(
                self.stack.enter_context(
                    ManagedProcess(
                        [
                            "kubectl",
                            "--namespace",
                            "default",
                            "logs",
                            "-f",
                            "sieve-client",
                            "-c",
                            route,
                            "--pod-running-timeout=60s",
                        ],
                        operation="continuous " + route + " client",
                        category="workload",
                        timeout=480,
                        cwd=repository_root(),
                        output=self.directory / ("client-" + route + ".jsonl"),
                    )
                )
            )

    def warm(self) -> bool:
        return all(
            len(records := self.records(route)) >= 3
            and all(
                r.get("kind") == "query" and r.get("ok") is True for r in records[-3:]
            )
            for route in ROUTES
        )

    def finish(self) -> dict[str, Any]:
        self.mark("stop_requested")
        for route in ROUTES:
            self.kubectl(
                "exec",
                "sieve-client",
                "-c",
                route,
                "--",
                "touch",
                "/tmp/sieve-client-stop",
            )
        for follower in self.followers:
            follower.wait(check=True)  # type: ignore[no-untyped-call]
        pod = None
        terminated = False
        end = time.monotonic() + 15
        with deadline(15):
            while time.monotonic() < end:
                pod = get("pod", "sieve-client")
                terminated = bool(
                    pod
                    and len(
                        statuses := pod.get("status", {}).get("containerStatuses", [])
                    )
                    == 2
                    and all(
                        s.get("state", {}).get("terminated", {}).get("exitCode") == 0
                        for s in statuses
                    )
                )
                if terminated:
                    break
                time.sleep(min(0.2, max(0, end - time.monotonic())))
        metrics = {}
        for route in ROUTES:
            records = self.records(route)
            queries = [r for r in records if r.get("kind") == "query"]
            successes = [
                r
                for r in queries
                if r["returncode"] == 0 and r["stdout"].strip() == "32|528|32"
            ]
            failures = [r for r in queries if r not in successes]
            tail = [
                r
                for r in queries
                if r["started_ns"] >= self.events["converged"]["wall_ns"]
            ]
            complete = bool(
                terminated
                and records
                and records[-1].get("kind") == "terminal"
                and records[-1].get("reason") == "stop"
                and records[-1].get("attempts") == len(queries)
                and len(records) == len(queries) + 1
                and [r["seq"] for r in queries] == list(range(1, len(queries) + 1))
                and all(r["route"] == route for r in records)
                and len(queries) >= 6
                and queries[0]["started_ns"]
                < self.events["delete_requested"]["wall_ns"]
                and queries[-1]["ended_ns"]
                >= self.events["observation_complete"]["wall_ns"] - 2_000_000_000
                and all(
                    b["started_ns"] - a["ended_ns"] < 10_000_000_000
                    for a, b in zip(queries, queries[1:], strict=False)
                )
            )
            metrics[route] = {
                "complete": complete,
                "attempts": len(queries),
                "successes": len(successes),
                "failures": len(failures),
                "bad_results": sum(
                    r["returncode"] == 0 and r["stdout"].strip() != "32|528|32"
                    for r in queries
                ),
                "recovered": len(tail) >= 3 and all(r in successes for r in tail[-3:]),
                "final_pod": pod,
            }
        return metrics
