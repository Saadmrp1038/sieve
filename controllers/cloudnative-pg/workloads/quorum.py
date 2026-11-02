"""Observe automatic failover across a synchronous-quorum configuration change."""

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from sieve_tool.process import ProcessError, ProcessTimeout, deadline, execution, run
from sieve_tool.testing.child_repair import get, kubectl, owned_by

CLUSTER = "sieve-quorum"
CLIENT = "sieve-quorum-client"
PHASE = "sieve.testing/quorum-phase"
WITNESS = 1_000_000_000
QUERY = """SELECT json_build_object(
'recovery',pg_is_in_recovery(),
'synchronous_commit',current_setting('synchronous_commit'),
'synchronous_standby_names',current_setting('synchronous_standby_names'),
'sync_standbys',(SELECT count(*) FROM pg_stat_replication WHERE state='streaming'
 AND sync_state IN ('sync','quorum')),
'receive_lsn',pg_last_wal_receive_lsn(),'replay_lsn',pg_last_wal_replay_lsn(),
'timeline',(SELECT timeline_id FROM pg_control_checkpoint()),
'rows',(SELECT coalesce(json_agg(json_build_array(id,payload) ORDER BY id),'[]')
 FROM sieve_rolling));"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["failover-quorum-update"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--ready-timeout", type=float, default=300)
    parser.add_argument("--observe-seconds", type=float, default=120)
    parser.add_argument("--stable-seconds", type=float, default=30)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--warmup-seconds", type=float, default=10)
    parser.add_argument(
        "--postgres-image",
        default=os.environ.get(
            "SIEVE_CNPG_POSTGRES_IMAGE",
            "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie",
        ),
    )
    args = parser.parse_args(argv)
    if (
        any(
            not math.isfinite(v) or v <= 0
            for v in (
                args.ready_timeout,
                args.observe_seconds,
                args.stable_seconds,
                args.poll_interval,
                args.warmup_seconds,
            )
        )
        or args.observe_seconds > 120
    ):
        parser.error("time limits must be positive and finite; observation <=120s")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "status": "starting",
        "updates": [],
        "samples": [],
        "sql_samples": [],
        "network_actions": [],
        "single_replica_samples": [],
        "events": {},
    }
    checks: list[dict[str, Any]] = []
    isolated: dict[str, dict[str, Any]] = {}

    def save() -> None:
        (directory / "quorum-observations.json").write_text(
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

    def docker(*arguments: str) -> str:
        return run(
            ["docker", *arguments],
            capture_output=True,
            category="request",
            operation="owned quorum worker network",
            timeout=15,
        ).stdout.strip()

    def networks(node: str) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(
            docker("inspect", "--format", "{{json .NetworkSettings.Networks}}", node)
        )
        return result

    def isolate(node: str) -> None:
        labels = json.loads(
            docker("inspect", "--format", "{{json .Config.Labels}}", node)
        )
        if (
            labels.get("io.x-k8s.kind.cluster") != "kind"
            or labels.get("io.x-k8s.kind.role") != "worker"
        ):
            raise RuntimeError("Refusing to disconnect an unowned/non-worker node")
        attached = networks(node)
        if set(attached) != {"kind"} or not attached["kind"].get("IPAddress"):
            raise RuntimeError("Expected one owned Kind IPv4 network")
        isolated[node] = attached["kind"]
        row: dict[str, Any] = {
            "operation": "disconnect",
            "node": node,
            "started_ns": time.time_ns(),
        }
        evidence["network_actions"].append(row)
        row["before"] = attached
        save()
        docker("network", "disconnect", "kind", node)
        row["after"] = networks(node)
        row["ended_ns"] = time.time_ns()
        save()
        if "kind" in row["after"]:
            raise RuntimeError("Worker network disconnect did not take effect")

    def restore(node: str) -> None:
        attached = networks(node)
        row: dict[str, Any] = {
            "operation": "connect",
            "node": node,
            "started_ns": time.time_ns(),
        }
        evidence["network_actions"].append(row)
        row["before"] = attached
        save()
        original = isolated[node]
        if "kind" not in attached:
            options = ["--ip", original["IPAddress"]]
            if original.get("GlobalIPv6Address"):
                options += ["--ip6", original["GlobalIPv6Address"]]
            docker("network", "connect", *options, "kind", node)
        row["after"] = networks(node)
        row["ended_ns"] = time.time_ns()
        save()
        if row["after"].get("kind", {}).get("IPAddress") != original["IPAddress"]:
            raise RuntimeError("Worker network identity was not restored")
        del isolated[node]

    def sql(pod: str, query: str) -> str:
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
            query,
        ).strip()

    def snapshot(pod: str) -> dict[str, Any]:
        row: dict[str, Any] = json.loads(sql(pod, QUERY))
        evidence["sql_samples"].append(
            {"pod": pod, "wall_ns": time.time_ns(), "result": row}
        )
        save()
        return row

    def sample() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        cluster = get("clusters.postgresql.cnpg.io", CLUSTER)
        pods = json.loads(
            kubectl("get", "pods", "-l", "cnpg.io/cluster=" + CLUSTER, "-o", "json")
        )["items"]
        if (
            cluster is None
            or cluster["metadata"]["uid"]
            != evidence["initial_cluster"]["metadata"]["uid"]
        ):
            raise RuntimeError("Cluster changed identity or disappeared")
        evidence["samples"].append(
            {"wall_ns": time.time_ns(), "cluster": cluster, "pods": pods}
        )
        save()
        return cluster, pods

    def ready(pod: dict[str, Any]) -> bool:
        return not pod["metadata"].get("deletionTimestamp") and any(
            c.get("type") == "Ready" and c.get("status") == "True"
            for c in pod.get("status", {}).get("conditions", [])
        )

    def healthy(cluster: dict[str, Any], pods: list[dict[str, Any]]) -> bool:
        status = cluster.get("status", {})
        return bool(
            len(pods) == 3
            and status.get("readyInstances") == 3
            and status.get("currentPrimary")
            and status.get("currentPrimary") == status.get("targetPrimary")
            and all(owned_by(p, cluster) and ready(p) for p in pods)
        )

    def wait(predicate: Callable[[], Any], description: str) -> Any:
        end = time.monotonic() + args.ready_timeout
        with deadline(args.ready_timeout):
            while time.monotonic() < end:
                try:
                    result = predicate()
                    if result:
                        return result
                except ProcessTimeout:
                    raise
                except ProcessError:
                    # Each refused connection/timeout is retained in the command journal.
                    pass
                time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
        raise RuntimeError("Timed out: " + description)

    def wait_ready() -> tuple[dict[str, Any], list[dict[str, Any]]]:
        def predicate() -> Any:
            cluster, pods = sample()
            return (cluster, pods) if healthy(cluster, pods) else None

        return cast(
            tuple[dict[str, Any], list[dict[str, Any]]],
            wait(predicate, "three owned ready instances"),
        )

    def patch(phase: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        response: dict[str, Any] = json.loads(
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
                            "value": evidence["initial_cluster"]["metadata"]["uid"],
                        },
                        {
                            "op": "add",
                            "path": "/metadata/annotations",
                            "value": {PHASE: phase},
                        },
                        *changes,
                    ]
                ),
                "-o",
                "json",
            )
        )
        evidence["updates"].append(
            {"phase": phase, "wall_ns": time.time_ns(), "response": response}
        )
        save()
        return response

    def logs(stream: str, suffix: str = "") -> list[dict[str, Any]]:
        raw = kubectl("logs", CLIENT, "-c", stream)
        (directory / ("client-" + stream + suffix + ".jsonl")).write_text(raw)
        return [json.loads(line) for line in raw.splitlines()]

    with execution({"request": 15}, directory / "workload-commands"):
        try:
            nodes = json.loads(
                kubectl(
                    "get",
                    "nodes",
                    "-l",
                    "!node-role.kubernetes.io/control-plane",
                    "-o",
                    "json",
                )
            )["items"]
            if len(nodes) != 3:
                raise RuntimeError("Exactly three dedicated Kind workers are required")
            for node in nodes:
                kubectl(
                    "label",
                    "node",
                    node["metadata"]["name"],
                    "sieve.testing/quorum-worker=true",
                    "--overwrite",
                )
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
                            "affinity": {
                                "podAntiAffinityType": "required",
                                "nodeSelector": {"sieve.testing/quorum-worker": "true"},
                            },
                            "postgresql": {
                                "parameters": {"synchronous_commit": "remote_apply"},
                                "synchronous": {
                                    "method": "any",
                                    "number": 2,
                                    "dataDurability": "required",
                                    "failoverQuorum": False,
                                },
                                "pg_hba": ["host all sieve_probe all scram-sha-256"],
                            },
                        },
                    },
                )
            )
            save()
            cluster, pods = wait_ready()
            primary = cluster["status"]["currentPrimary"]
            replicas = sorted(
                p["metadata"]["name"] for p in pods if p["metadata"]["name"] != primary
            )
            a, b = replicas
            placements = {p["metadata"]["name"]: p["spec"]["nodeName"] for p in pods}
            if len(set(placements.values())) != 3:
                raise RuntimeError("Instances are not on three distinct workers")
            evidence.update(
                primary=primary,
                replica_A=a,
                replica_B=b,
                placements=placements,
                initial_pods=pods,
            )
            sql(
                primary,
                "CREATE ROLE sieve_probe LOGIN PASSWORD 'sieve-public-probe-fixture'; "
                "CREATE TABLE sieve_rolling(id integer PRIMARY KEY,payload text NOT NULL); "
                "GRANT SELECT,INSERT ON sieve_rolling TO sieve_probe;",
            )
            patch(
                "QUORUM_ENABLED",
                [
                    {
                        "op": "replace",
                        "path": "/spec/postgresql/synchronous/failoverQuorum",
                        "value": True,
                    }
                ],
            )

            def effective(number: int) -> Any:
                row = snapshot(primary)
                quorum = get("failoverquorums.postgresql.cnpg.io", CLUSTER)
                evidence.setdefault("quorum_samples", []).append(
                    {"wall_ns": time.time_ns(), "object": quorum}
                )
                save()
                status = (quorum or {}).get("status", {})
                return (
                    (row, quorum)
                    if (
                        not row["recovery"]
                        and row["synchronous_commit"] == "remote_apply"
                        and row["synchronous_standby_names"]
                        .upper()
                        .startswith(f"ANY {number} (")
                        and row["sync_standbys"] >= number
                        and status.get("standbyNumber") == number
                        and set(status.get("standbyNames", [])) == {a, b}
                        and status.get("primary") == primary
                    )
                    else None
                )

            row, quorum = wait(
                lambda: effective(2), "effective ANY 2 and quorum metadata"
            )
            evidence.update(
                initial_sql={
                    p["metadata"]["name"]: snapshot(p["metadata"]["name"]) for p in pods
                },
                initial_quorum=quorum,
            )
            check("required_ANY_2_effective_before_writes", True)
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
                            "nodeName": "kind-control-plane",
                            "tolerations": [{"operator": "Exists"}],
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
                                        "--duration",
                                        "900",
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
            save()
            kubectl("wait", "--for=condition=Ready", "pod/" + CLIENT, "--timeout=60s")
            time.sleep(args.warmup_seconds)
            for stream in ("read", "write"):
                if sum(r.get("ok", False) for r in logs(stream, "-warmup")) < 3:
                    raise RuntimeError("Client warmup failed: " + stream)
            mark("warmup_complete")
            try:
                with deadline(60):
                    isolate(placements[a])
                    patch(
                        "LOWER_SYNC",
                        [
                            {
                                "op": "replace",
                                "path": "/spec/postgresql/synchronous/number",
                                "value": 1,
                            }
                        ],
                    )
                    row, quorum = wait(
                        lambda: effective(1),
                        "effective ANY 1 and authoritative metadata",
                    )
                    evidence.update(lower_sql=row, lower_quorum=quorum)
                    started = time.time_ns()
                    receipt = sql(
                        primary,
                        "BEGIN; SET LOCAL synchronous_commit='remote_apply'; "
                        f"INSERT INTO sieve_rolling VALUES ({WITNESS},'ack-{WITNESS}'); "
                        f"COMMIT; SELECT 'ACK:{WITNESS}';",
                    )
                    evidence["witness"] = {
                        "id": WITNESS,
                        "started_ns": started,
                        "ended_ns": time.time_ns(),
                        "stdout": receipt,
                        "acknowledged": receipt == f"ACK:{WITNESS}",
                    }
                    save()
                    if not evidence["witness"]["acknowledged"]:
                        raise RuntimeError("Witness transaction was not acknowledged")
                    witness_b = snapshot(b)
                    evidence["witness_on_B"] = witness_b
                    if dict(witness_b["rows"]).get(WITNESS) != f"ack-{WITNESS}":
                        raise RuntimeError("Acknowledged witness is not present on B")
                with deadline(210):
                    isolate(placements[primary])
                    isolate(placements[b])
                    restore(placements[a])
                    mark("only_A_network_connected")

                    def single() -> Any:
                        c, ps = sample()
                        by_name = {p["metadata"]["name"]: p for p in ps}
                        if (
                            set(by_name) != set(placements)
                            or not ready(by_name[a])
                            or any(ready(by_name[n]) for n in (primary, b))
                        ):
                            return None
                        result = snapshot(a)
                        return c, result

                    c, result = wait(single, "A ready and both other Pods unready")
                    evidence["first_single_replica"] = {"cluster": c, "sql": result}
                    check(
                        "survivor_lacks_acknowledged_witness_before_recovery",
                        WITNESS not in dict(result["rows"]),
                    )
                    mark("single_replica_observation_started")
                    end = time.monotonic() + args.observe_seconds
                    while time.monotonic() < end:
                        c, ps = sample()
                        result = snapshot(a)
                        evidence["single_replica_samples"].append(
                            {
                                "wall_ns": time.time_ns(),
                                "cluster": c,
                                "pods": ps,
                                "sql": result,
                            }
                        )
                        save()
                        time.sleep(
                            min(args.poll_interval, max(0, end - time.monotonic()))
                        )
                    mark("single_replica_observation_finished")
                    observations = [
                        evidence["first_single_replica"],
                        *evidence["single_replica_samples"],
                    ]
                    check(
                        "no_automatic_failover_without_current_quorum",
                        all(
                            r["sql"]["recovery"]
                            and r["cluster"]["status"].get("currentPrimary") == primary
                            and r["cluster"]["status"].get("targetPrimary") == primary
                            for r in observations
                        ),
                    )
                    patch("OBSERVATION_FINISHED", [])
            finally:
                errors = []
                with deadline(60, fresh=True):
                    for node in list(isolated):
                        try:
                            restore(node)
                        except Exception as error:
                            errors.append(str(error))
                evidence["network_restore_errors"] = errors
                save()
                if errors:
                    raise RuntimeError(
                        "Worker network restoration failed: " + repr(errors)
                    )
            mark("workers_restored")
            cluster, pods = wait_ready()
            mark("converged")
            end = time.monotonic() + args.stable_seconds
            stable = True
            while time.monotonic() < end:
                cluster, pods = sample()
                stable = stable and healthy(cluster, pods)
                time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
            mark("stable_observation_finished")
            for stream in ("read", "write"):
                kubectl("exec", CLIENT, "-c", stream, "--", "touch", "/tmp/sieve-stop")

            def stopped() -> Any:
                obj = get("pod", CLIENT)
                statuses = (obj or {}).get("status", {}).get("containerStatuses", [])
                return (
                    obj
                    if len(statuses) == 2
                    and all(
                        s.get("state", {}).get("terminated", {}).get("exitCode") == 0
                        for s in statuses
                    )
                    else None
                )

            evidence["client_final_pod"] = wait(stopped, "complete client ledgers")
            streams = {stream: logs(stream) for stream in ("read", "write")}
            for stream, records in streams.items():
                attempts = [r for r in records if r["kind"] == "attempt"]
                warm = [
                    json.loads(line)
                    for line in (directory / ("client-" + stream + "-warmup.jsonl"))
                    .read_text()
                    .splitlines()
                ]
                check(
                    stream + "_stream_complete",
                    bool(attempts)
                    and len(records) == len(attempts) + 1
                    and records[-1].get("reason") == "stop"
                    and records[-1].get("attempts") == len(attempts)
                    and [r["seq"] for r in attempts]
                    == list(range(1, len(attempts) + 1))
                    and records[: len(warm)] == warm
                    and all(r["stream"] == stream for r in records)
                    and attempts[0]["started_ns"]
                    < evidence["events"]["warmup_complete"]["wall_ns"]
                    and attempts[-1]["ended_ns"]
                    >= evidence["events"]["stable_observation_finished"]["wall_ns"]
                    - 2_000_000_000
                    and all(
                        b["monotonic_started_ns"] - a["monotonic_ended_ns"]
                        < 10_000_000_000
                        for a, b in zip(attempts, attempts[1:], strict=False)
                    ),
                )
                check(
                    stream + "_recovered_after_convergence",
                    sum(
                        r["started_ns"] >= evidence["events"]["converged"]["wall_ns"]
                        and r["ok"]
                        for r in attempts
                    )
                    >= 3,
                )
            check(
                "no_successful_reads_with_bad_payloads",
                all(r["returncode"] != 0 or r["ok"] for r in streams["read"][:-1]),
            )
            evidence["acknowledged_ids"] = [
                r["seq"] for r in streams["write"][:-1] if r["acknowledged"]
            ] + [WITNESS]
            evidence["ambiguous_ids"] = [
                r["seq"] for r in streams["write"][:-1] if not r["acknowledged"]
            ]
            evidence.update(
                final_cluster=cluster,
                final_pods=pods,
                final_sql={
                    p["metadata"]["name"]: snapshot(p["metadata"]["name"]) for p in pods
                },
            )
            expected = {i: f"ack-{i}" for i in evidence["acknowledged_ids"]}
            end = time.monotonic() + min(60, args.ready_timeout)
            while True:
                missing = {
                    name: [
                        i
                        for i, payload in expected.items()
                        if dict(r["rows"]).get(i) != payload
                    ]
                    for name, r in evidence["final_sql"].items()
                }
                if not any(missing.values()) or time.monotonic() >= end:
                    break
                time.sleep(args.poll_interval)
                evidence["final_sql"] = {
                    p["metadata"]["name"]: snapshot(p["metadata"]["name"]) for p in pods
                }
            evidence["missing_acknowledged_ids"] = missing
            check(
                "acknowledged_writes_preserved_on_all_instances",
                bool(expected) and not any(missing.values()),
            )
            final_primary = cluster["status"]["currentPrimary"]
            final = evidence["final_sql"]
            check(
                "required_ANY_1_and_final_roles",
                sum(not r["recovery"] for r in final.values()) == 1
                and not final[final_primary]["recovery"]
                and final[final_primary]["sync_standbys"] >= 1
                and all(
                    r["synchronous_commit"] == "remote_apply"
                    and r["synchronous_standby_names"].upper().startswith("ANY 1 (")
                    for r in final.values()
                ),
            )
            check(
                "latest_accepted_cluster_spec",
                cluster["spec"] == evidence["updates"][-1]["response"]["spec"],
            )
            check("workers_restored", not isolated)
            check("stable_after_recovery", stable)
            check(
                "witness_preserved_on_all_instances",
                all(
                    dict(r["rows"]).get(WITNESS) == f"ack-{WITNESS}"
                    for r in evidence["final_sql"].values()
                ),
            )
            evidence["status"] = (
                "passed" if all(c["passed"] for c in checks) else "semantic_failure"
            )
            save()
            args.output.write_text("FINISH-SIEVE-TEST\n")
            return 0 if evidence["status"] == "passed" else 1
        except Exception as error:
            evidence.update(status="incomplete", error=str(error))
            check("quorum_workload_completed", False)
            args.output.write_text("ERROR: " + str(error) + "\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
