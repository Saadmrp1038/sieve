"""Run one managed-child mutation and retain evidence of recovery or failure."""

import argparse
import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sieve_tool.paths import repository_root
from sieve_tool.process import ProcessTimeout, deadline, execution
from sieve_tool.process import run as run_process

type Resource = dict[str, Any]
type Observation = dict[str, Resource | None]
type Wait = Callable[[Callable[[], bool], str], None]


@dataclass(frozen=True)
class Mutation:
    response: Resource
    observed: bool = True


def delete_child(plural: str, name: str, uid: str) -> Mutation:
    response = kubectl(
        "delete",
        "--raw",
        f"/api/v1/namespaces/default/{plural}/{name}",
        "-f",
        "-",
        payload={
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": uid},
        },
    )
    return Mutation(json.loads(response))


def kubectl(*arguments: str, payload: Resource | None = None) -> str:
    return run_process(
        ["kubectl", "--namespace", "default", *arguments],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        category="request",
        operation="managed-child repair kubectl",
        cwd=repository_root(),
    ).stdout


def get(kind: str, name: str) -> Resource | None:
    raw = kubectl("get", kind, name, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw.strip() else None


class RepairTarget(Protocol):
    """Operator rules; the runner owns waits, identity checks, and artifacts."""

    def setup(self, wait: Wait) -> None: ...

    def observe(self) -> Observation: ...

    def healthy(self, observation: Mapping[str, Resource | None]) -> bool: ...

    def mutate(self, workload: str, child: Resource) -> Mutation:
        """Return the response and whether it establishes the intended drift."""
        ...


def owned_by(child: Resource, parent: Resource) -> bool:
    return any(
        ref.get("uid") == parent["metadata"]["uid"]
        and ref.get("kind") == parent["kind"]
        and ref.get("name") == parent["metadata"]["name"]
        and ref.get("controller") is True
        for ref in child["metadata"].get("ownerReferences", [])
    )


def run(arguments: Sequence[str], target: RepairTarget, *, timeout: float) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["delete-child", "drift-child"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--repair-timeout", type=float, default=timeout)
    parser.add_argument("--settle-seconds", type=float, default=15)
    args = parser.parse_args(arguments)
    if not math.isfinite(args.repair_timeout) or args.repair_timeout <= 0:
        parser.error("repair timeout must be finite and positive")
    if not math.isfinite(args.settle_seconds) or args.settle_seconds < 0:
        parser.error("settling period must be finite and nonnegative")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"workload": args.workload, "status": "setup_failed"}

    def wait_for(predicate: Callable[[], bool], description: str) -> None:
        end = time.monotonic() + args.repair_timeout
        with deadline(args.repair_timeout):
            while time.monotonic() < end:
                try:
                    if predicate():
                        return
                except ProcessTimeout:
                    if time.monotonic() >= end:
                        break
                    raise
                time.sleep(min(1, max(0, end - time.monotonic())))
        raise RuntimeError("Timed out: " + description)

    def observe(*, repairing: bool = False) -> bool:
        observation = target.observe()
        evidence.update(observation)
        parent, child = observation["parent_after"], observation["child_after"]
        if repairing:
            before = evidence["parent_before"]
            if (
                parent is None
                or any(
                    before["metadata"][key] != parent["metadata"][key]
                    for key in ("uid", "generation")
                )
                or before["spec"] != parent["spec"]
            ):
                evidence["status"] = "parent_changed"
                raise RuntimeError("Parent changed during child repair")
        if parent is None or child is None or not owned_by(child, parent):
            return False
        if (
            repairing
            and args.workload == "delete-child"
            and (
                child["metadata"]["uid"] == evidence["child_before"]["metadata"]["uid"]
            )
        ):
            return False
        return target.healthy(observation)

    with execution(log_dir=args.output.parent / "repair-commands"):
        try:
            target.setup(wait_for)
            wait_for(observe, "initial convergence")
            evidence["parent_before"] = evidence["parent_after"]
            evidence["child_before"] = evidence["child_after"]
            evidence["status"] = "mutation_failed"
            mutation = target.mutate(args.workload, evidence["child_before"])
            response = mutation.response
            evidence["mutation_response"] = response
            if not mutation.observed:
                raise RuntimeError(
                    "Field drift was not observed in the mutation response"
                )
            response_uid = response.get("metadata", {}).get("uid") or response.get(
                "details", {}
            ).get("uid")
            if response_uid != evidence["child_before"]["metadata"]["uid"]:
                raise RuntimeError(
                    "Mutation response does not identify the original child"
                )
            evidence["status"] = "repair_timeout"
            wait_for(lambda: observe(repairing=True), "owned child repair")
            evidence["status"] = "repair_lost"
            settled_at = time.monotonic() + args.settle_seconds
            while time.monotonic() < settled_at:
                time.sleep(min(1, max(0, settled_at - time.monotonic())))
                if not observe(repairing=True):
                    raise RuntimeError("Child repair did not remain consistent")
            evidence["settle_seconds"] = args.settle_seconds
            evidence["status"] = "repaired"
        except Exception as error:
            evidence["error"] = str(error)
            args.output.write_text(str(error) + "\n")
            return 1
        finally:
            args.output.with_name("repair-observations.json").write_text(
                json.dumps(evidence, indent=2) + "\n"
            )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0
