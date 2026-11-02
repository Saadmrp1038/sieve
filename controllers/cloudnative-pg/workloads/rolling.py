"""Observe overlapping restart-required updates with continuous client receipts."""

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from sieve_tool.process import ProcessError, deadline, execution
from sieve_tool.testing.child_repair import get, kubectl, owned_by

CLUSTER = "sieve-rolling"
CLIENT = "sieve-rolling-client"
PHASE = "sieve.testing/rolling-phase"
QUERY = """SELECT json_build_object(
'max_connections',current_setting('max_connections'),
'pending_restart',(SELECT bool_or(pending_restart) FROM pg_settings),
'recovery',pg_is_in_recovery(),'postmaster_start',pg_postmaster_start_time(),
'synchronous_commit',current_setting('synchronous_commit'),
'synchronous_standby_names',current_setting('synchronous_standby_names'),
'sync_standbys',(SELECT count(*) FROM pg_stat_replication WHERE state='streaming'
 AND sync_state IN ('sync','quorum')),
'rows',(SELECT coalesce(json_agg(json_build_array(id,payload) ORDER BY id),'[]')
 FROM sieve_rolling));"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["rolling-config-update"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--ready-timeout", type=float, default=300)
    parser.add_argument("--observe-seconds", type=float, default=30)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--update-interval", type=float, default=4)
    parser.add_argument("--warmup-seconds", type=float, default=10)
    parser.add_argument(
        "--postgres-image",
        default=os.environ.get(
            "SIEVE_CNPG_POSTGRES_IMAGE",
            "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie",
        ),
    )
    args = parser.parse_args(argv)
    if any(
        not math.isfinite(v) or v <= 0
        for v in (
            args.ready_timeout,
            args.observe_seconds,
            args.poll_interval,
            args.update_interval,
            args.warmup_seconds,
        )
    ):
        parser.error("time limits must be finite and positive")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "status": "starting",
        "updates": [],
        "samples": [],
        "sql_samples": [],
        "events": {},
    }
    checks: list[dict[str, Any]] = []

    def save() -> None:
        (directory / "rolling-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        (directory / "semantic-outcome.json").write_text(
            json.dumps({"schema_version": 1, "checks": checks}, indent=2) + "\n"
        )

    def mark(name: str) -> None:
        evidence["events"][name] = {
            "wall_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(),
        }
        save()

    def check(name: str, passed: bool) -> None:
        checks.append({"name": name, "passed": passed})
        save()

    def sql(pod: str, statement: str) -> str:
        return kubectl(
            "exec",
            pod,
            "-c",
            "postgres",
            "--",
            "psql",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-Atc",
            statement,
        ).strip()

    def sample() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cluster = get("clusters.postgresql.cnpg.io", CLUSTER)
        pods = json.loads(
            kubectl("get", "pods", "-l", "cnpg.io/cluster=" + CLUSTER, "-o", "json")
        )["items"]
        evidence["samples"].append(
            {"wall_ns": time.time_ns(), "cluster": cluster, "pods": pods}
        )
        save()
        if (
            cluster is None
            or cluster["metadata"]["uid"]
            != evidence["initial_cluster"]["metadata"]["uid"]
        ):
            raise RuntimeError("Cluster disappeared or changed identity")
        return cluster, pods

    def healthy(cluster: dict[str, Any], pods: list[dict[str, Any]]) -> bool:
        status = cluster.get("status", {})
        return bool(
            status.get("readyInstances") == 3
            and status.get("currentPrimary")
            and status.get("currentPrimary") == status.get("targetPrimary")
            and len(pods) == 3
            and all(
                owned_by(p, cluster)
                and not p["metadata"].get("deletionTimestamp")
                and any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in p.get("status", {}).get("conditions", [])
                )
                for p in pods
            )
        )

    def snapshot(pods: list[dict[str, Any]]) -> dict[str, Any]:
        rows = {
            p["metadata"]["name"]: json.loads(sql(p["metadata"]["name"], QUERY))
            for p in pods
        }
        evidence["sql_samples"].append({"wall_ns": time.time_ns(), "instances": rows})
        save()
        return rows

    def valid(rows: dict[str, Any], setting: str, primary: str) -> bool:
        return bool(
            len(rows) == 3
            and primary in rows
            and not rows[primary]["recovery"]
            and sum(not r["recovery"] for r in rows.values()) == 1
            and rows[primary]["sync_standbys"] >= 1
            and rows[primary]["synchronous_standby_names"].upper().startswith("ANY 1 (")
            and all(
                r["max_connections"] == setting
                and not r["pending_restart"]
                and r["synchronous_commit"] == "remote_apply"
                for r in rows.values()
            )
        )

    def wait_ready(
        setting: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        end = time.monotonic() + args.ready_timeout
        with deadline(args.ready_timeout):
            while time.monotonic() < end:
                cluster, pods = sample()
                if healthy(cluster, pods):
                    if setting is None:
                        return cluster, pods, {}
                    try:
                        rows = snapshot(pods)
                        if valid(rows, setting, cluster["status"]["currentPrimary"]):
                            return cluster, pods, rows
                    except ProcessError:
                        # Expected connection loss while PostgreSQL restarts; raw command retained.
                        pass
                time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
        raise RuntimeError(
            "Timed out waiting for three healthy instances at setting " + str(setting)
        )

    def logs(stream: str, suffix: str = "") -> list[dict[str, Any]]:
        raw = kubectl("logs", CLIENT, "-c", stream)
        (directory / ("client-" + stream + suffix + ".jsonl")).write_text(raw)
        return [json.loads(line) for line in raw.splitlines()]

    with execution(log_dir=directory / "workload-commands"):
        try:
            evidence["initial_cluster"] = json.loads(
                kubectl(
                    "apply",
                    "-f",
                    "-",
                    "-o",
                    "json",
                    payload={
                        "apiVersion": "postgresql.cnpg.io/v1",
                        "kind": "Cluster",
                        "metadata": {"name": CLUSTER, "annotations": {PHASE: "A"}},
                        "spec": {
                            "instances": 3,
                            "imageName": args.postgres_image,
                            "storage": {"size": "1Gi"},
                            "resources": {
                                "requests": {"memory": "256Mi", "cpu": "100m"}
                            },
                            "postgresql": {
                                "parameters": {
                                    "max_connections": "100",
                                    "synchronous_commit": "remote_apply",
                                },
                                "synchronous": {
                                    "method": "any",
                                    "number": 1,
                                    "dataDurability": "required",
                                },
                                "pg_hba": ["host all sieve_probe all scram-sha-256"],
                            },
                        },
                    },
                )
            )
            save()
            cluster, pods, _ = wait_ready()
            sql(
                cluster["status"]["currentPrimary"],
                "CREATE ROLE sieve_probe LOGIN PASSWORD 'sieve-public-probe-fixture'; "
                "CREATE TABLE sieve_rolling(id integer PRIMARY KEY,payload text NOT NULL); "
                "GRANT SELECT,INSERT ON sieve_rolling TO sieve_probe;",
            )
            cluster, pods, rows = wait_ready("100")
            evidence.update(
                initial_ready_cluster=cluster, initial_pods=pods, initial_sql=rows
            )
            check("required_synchronous_replication_effective_before_writes", True)
            script = Path(__file__).with_name("rolling_client.py").read_text()
            evidence["client_pod"] = json.loads(
                kubectl(
                    "create",
                    "-f",
                    "-",
                    "-o",
                    "json",
                    payload={
                        "apiVersion": "v1",
                        "kind": "Pod",
                        "metadata": {"name": CLIENT},
                        "spec": {
                            "restartPolicy": "Never",
                            "automountServiceAccountToken": False,
                            "containers": [
                                {
                                    "name": stream,
                                    "image": args.postgres_image,
                                    "command": ["python3", "-u", "-c", script],
                                    "args": [
                                        "--stream",
                                        stream,
                                        "--host",
                                        CLUSTER + "-rw.default.svc",
                                    ],
                                    "env": [
                                        {
                                            "name": "PGPASSWORD",
                                            "value": "sieve-public-probe-fixture",
                                        },
                                        {"name": "PGCONNECT_TIMEOUT", "value": "2"},
                                        {
                                            "name": "PGOPTIONS",
                                            "value": "-c statement_timeout=4000",
                                        },
                                    ],
                                    "resources": {
                                        "requests": {"memory": "32Mi", "cpu": "20m"}
                                    },
                                }
                                for stream in ("read", "write")
                            ],
                        },
                    },
                )
            )
            kubectl("wait", "--for=condition=Ready", "pod/" + CLIENT, "--timeout=60s")
            time.sleep(args.warmup_seconds)
            for stream in ("read", "write"):
                records = logs(stream, "-warmup")
                if sum(r.get("ok", False) for r in records) < 3:
                    raise RuntimeError("Client warmup failed: " + stream)
            mark("first_update_requested")
            for phase, setting in (("B", "120"), ("C", "140")):
                response = json.loads(
                    kubectl(
                        "patch",
                        "clusters.postgresql.cnpg.io",
                        CLUSTER,
                        "--type=json",
                        "-p",
                        json.dumps(
                            [
                                {
                                    "op": "test",
                                    "path": "/metadata/uid",
                                    "value": evidence["initial_cluster"]["metadata"][
                                        "uid"
                                    ],
                                },
                                {
                                    "op": "replace",
                                    "path": "/spec/postgresql/parameters/max_connections",
                                    "value": setting,
                                },
                                {
                                    "op": "add",
                                    "path": "/metadata/annotations",
                                    "value": {PHASE: phase},
                                },
                            ]
                        ),
                        "-o",
                        "json",
                    )
                )
                if (
                    response["spec"]["postgresql"]["parameters"]["max_connections"]
                    != setting
                ):
                    raise RuntimeError("Configuration update was not accepted")
                evidence["updates"].append(
                    {"wall_ns": time.time_ns(), "phase": phase, "response": response}
                )
                save()
                if phase == "B":
                    sample()
                    time.sleep(args.update_interval)
                    sample()
            mark("latest_update_accepted")
            cluster, pods, rows = wait_ready("140")
            mark("converged")
            end = time.monotonic() + args.observe_seconds
            stable = True
            with deadline(args.ready_timeout):
                while time.monotonic() < end:
                    cluster, pods = sample()
                    try:
                        rows = snapshot(pods)
                        stable = (
                            stable
                            and healthy(cluster, pods)
                            and valid(rows, "140", cluster["status"]["currentPrimary"])
                        )
                    except ProcessError:
                        stable = False
                    time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
            mark("observation_complete")
            for stream in ("read", "write"):
                kubectl("exec", CLIENT, "-c", stream, "--", "touch", "/tmp/sieve-stop")
            end = time.monotonic() + 15
            while time.monotonic() < end:
                client = get("pod", CLIENT)
                statuses = (client or {}).get("status", {}).get("containerStatuses", [])
                if len(statuses) == 2 and all(
                    s.get("state", {}).get("terminated", {}).get("exitCode") == 0
                    for s in statuses
                ):
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Client streams did not terminate cleanly")
            evidence["client_final_pod"] = client
            streams = {stream: logs(stream) for stream in ("read", "write")}
            for stream, records in streams.items():
                attempts = [r for r in records if r["kind"] == "attempt"]
                complete = bool(
                    attempts
                    and records[-1].get("reason") == "stop"
                    and records[-1].get("attempts") == len(attempts)
                    and len(records) == len(attempts) + 1
                    and [r["seq"] for r in attempts]
                    == list(range(1, len(attempts) + 1))
                    and all(r["stream"] == stream for r in records)
                    and attempts[0]["started_ns"]
                    < evidence["events"]["first_update_requested"]["wall_ns"]
                    and attempts[-1]["ended_ns"]
                    >= evidence["events"]["observation_complete"]["wall_ns"]
                    - 2_000_000_000
                    and all(
                        b["monotonic_started_ns"] - a["monotonic_ended_ns"]
                        < 10_000_000_000
                        for a, b in zip(attempts, attempts[1:], strict=False)
                    )
                )
                check(stream + "_stream_complete", complete)
                tail = [
                    r
                    for r in attempts
                    if r["started_ns"] >= evidence["events"]["converged"]["wall_ns"]
                ]
                check(
                    stream + "_recovered_after_convergence",
                    len(tail) >= 3 and all(r["ok"] for r in tail[-3:]),
                )
            check(
                "no_successful_reads_with_bad_data",
                not any(
                    r.get("kind") == "attempt" and r["returncode"] == 0 and not r["ok"]
                    for r in streams["read"]
                ),
            )
            writes = streams["write"][:-1]
            evidence["acknowledged_ids"] = [
                r["seq"] for r in writes if r["acknowledged"]
            ]
            evidence["ambiguous_ids"] = [
                r["seq"] for r in writes if not r["acknowledged"]
            ]
            cluster, pods, _ = wait_ready("140")
            # Stop first, then wait for every acknowledged row on every instance.
            end = time.monotonic() + args.ready_timeout
            expected = {i: "ack-" + str(i) for i in evidence["acknowledged_ids"]}
            while True:
                rows = snapshot(pods)
                missing = {
                    name: [
                        i
                        for i, payload in expected.items()
                        if dict(r["rows"]).get(i) != payload
                    ]
                    for name, r in rows.items()
                }
                if not any(missing.values()) or time.monotonic() >= end:
                    break
                time.sleep(args.poll_interval)
            evidence.update(
                final_cluster=cluster,
                final_pods=pods,
                final_sql=rows,
                missing_acknowledged_ids=missing,
            )
            check(
                "acknowledged_writes_preserved_on_all_instances",
                bool(expected) and not any(missing.values()),
            )
            check(
                "latest_effective_configuration_and_roles",
                valid(rows, "140", cluster["status"]["currentPrimary"]),
            )
            check(
                "all_instances_restarted",
                all(
                    name in evidence["initial_sql"]
                    and r["postmaster_start"]
                    != evidence["initial_sql"][name]["postmaster_start"]
                    for name, r in rows.items()
                ),
            )
            check(
                "cluster_matches_latest_accepted_spec",
                cluster["spec"] == evidence["updates"][-1]["response"]["spec"]
                and cluster["metadata"]["generation"]
                == evidence["updates"][-1]["response"]["metadata"]["generation"],
            )
            check("stable_after_convergence", stable)
            evidence["status"] = (
                "passed" if all(c["passed"] for c in checks) else "semantic_failure"
            )
            save()
            args.output.write_text("FINISH-SIEVE-TEST\n")
            return 0 if evidence["status"] == "passed" else 1
        except Exception as error:
            evidence.update(status="incomplete", error=str(error))
            check("rolling_workload_completed", False)
            args.output.write_text("ERROR: " + str(error) + "\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
