"""Replace a Pooler identity while an old reconciliation may still be running."""

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from clients import Clients

from sieve_tool.process import ProcessError, ProcessTimeout, deadline, execution
from sieve_tool.testing.child_repair import Resource, get
from sieve_tool.testing.child_repair import kubectl as run_kubectl

CLUSTER = "sieve-db"
POOLER = "sieve-pooler"
PHASE = "sieve.testing/replacement-phase"
EXPECTED_ROWS = [[i, f"ack-{i}"] for i in range(1, 33)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workload", choices=["pooler-replacement", "pooler-replacement-clients"]
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--ready-timeout", type=float, default=300)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--replacement-delay", type=float, default=10)
    parser.add_argument("--observe-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if any(
        not math.isfinite(v) or v <= 0
        for v in (
            args.ready_timeout,
            args.poll_interval,
            args.replacement_delay,
            args.observe_seconds,
        )
    ):
        parser.error("time limits must be finite and positive")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {"status": "setup", "samples": [], "api_retries": []}
    checks: list[dict[str, Any]] = []

    def save() -> None:
        (directory / "replacement-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        if checks:
            (directory / "semantic-outcome.json").write_text(
                json.dumps({"schema_version": 1, "checks": checks}, indent=2) + "\n"
            )

    def kubectl(*arguments: str, payload: Resource | None = None) -> str:
        end = time.monotonic() + args.ready_timeout
        with deadline(args.ready_timeout):
            while True:
                try:
                    return run_kubectl(*arguments, payload=payload)
                except ProcessError as error:
                    if error.returncode != 1 or error.log is None:
                        raise
                    log = Path(error.log)
                    message = log.with_suffix(".stderr.log").read_text()
                    if not (
                        message.startswith("Error from server (InternalError):")
                        and 'failed calling webhook "vpooler.cnpg.io"' in message
                        and "failed to call webhook:" in message
                        and "cnpg-webhook-service.default.svc" in message
                        and "connect: connection refused" in message
                        and not log.with_suffix(".stdout.log").read_text().strip()
                    ):
                        raise
                    evidence["api_retries"].append(
                        {
                            "operation": arguments[0],
                            "command_log": str(log.relative_to(directory)),
                        }
                    )
                    save()
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise
                    time.sleep(min(args.poll_interval, remaining))

    def wait[T](predicate: Callable[[], T | None], label: str) -> T:
        end = time.monotonic() + args.ready_timeout
        with deadline(args.ready_timeout):
            while time.monotonic() < end:
                try:
                    value = predicate()
                except ProcessTimeout:
                    if time.monotonic() >= end:
                        break
                    raise
                if value and time.monotonic() < end:
                    return value
                time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
        raise RuntimeError("Timed out: " + label)

    def owned(child: Resource | None, uid: str) -> bool:
        return bool(
            child
            and not child["metadata"].get("deletionTimestamp")
            and any(
                ref.get("uid") == uid
                and ref.get("kind") == "Pooler"
                and ref.get("name") == POOLER
                and ref.get("controller") is True
                for ref in child["metadata"].get("ownerReferences", [])
            )
        )

    def sql(pod: str, statement: str) -> str:
        return kubectl(
            "exec",
            pod,
            "-c",
            "postgres",
            "--",
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-Atc",
            statement,
        ).strip()

    def pooler_document(instances: int, phase: str) -> Resource:
        return {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Pooler",
            "metadata": {"name": POOLER, "annotations": {PHASE: phase}},
            "spec": {
                "cluster": {"name": CLUSTER},
                "instances": instances,
                "type": "rw",
                "pgbouncer": {
                    "poolMode": "transaction",
                    "image": os.environ.get(
                        "SIEVE_CNPG_PGBOUNCER_IMAGE",
                        "ghcr.io/cloudnative-pg/pgbouncer:1.25.1",
                    ),
                },
            },
        }

    def phase_patch(uid: str, phase: str) -> Resource:
        response: Resource = json.loads(
            kubectl(
                "patch",
                "poolers.postgresql.cnpg.io",
                POOLER,
                "--type=json",
                "-p",
                json.dumps(
                    [
                        {"op": "test", "path": "/metadata/uid", "value": uid},
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
        return response

    with execution(log_dir=directory / "workload-commands"), ExitStack() as stack:
        clients = (
            Clients(directory, stack, kubectl)
            if args.workload == "pooler-replacement-clients"
            else None
        )
        if clients:
            evidence["client_events"] = clients.events
        try:
            kubectl(
                "apply",
                "-f",
                "-",
                "-o",
                "json",
                payload={
                    "apiVersion": "postgresql.cnpg.io/v1",
                    "kind": "Cluster",
                    "metadata": {"name": CLUSTER},
                    "spec": {
                        "instances": 3,
                        "imageName": os.environ.get(
                            "SIEVE_CNPG_POSTGRES_IMAGE",
                            "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie",
                        ),
                        "storage": {"size": "1Gi"},
                        "resources": {"requests": {"memory": "256Mi", "cpu": "100m"}},
                    },
                },
            )

            def cluster_ready() -> Resource | None:
                obj = get("clusters.postgresql.cnpg.io", CLUSTER)
                status = (obj or {}).get("status", {})
                return (
                    obj
                    if status.get("readyInstances") == 3
                    and status.get("currentPrimary")
                    and status.get("currentPrimary") == status.get("targetPrimary")
                    else None
                )

            cluster = wait(cluster_ready, "three PostgreSQL instances")
            evidence["cluster_before"] = cluster
            primary = cluster["status"]["currentPrimary"]
            sql(
                primary,
                "CREATE TABLE sieve_benchmark(id integer PRIMARY KEY,payload text NOT NULL); INSERT INTO sieve_benchmark SELECT i,'ack-'||i FROM generate_series(1,32) AS i;",
            )
            query = "SELECT json_agg(json_build_array(id,payload) ORDER BY id)::text FROM sieve_benchmark;"
            evidence["acknowledged_rows"] = json.loads(sql(primary, query))
            if evidence["acknowledged_rows"] != EXPECTED_ROWS:
                raise RuntimeError("Initial SQL acknowledgments differ")
            if clients:
                sql(
                    primary,
                    "CREATE ROLE sieve_probe LOGIN PASSWORD 'sieve-public-probe-fixture'; GRANT CONNECT ON DATABASE postgres TO sieve_probe; GRANT USAGE ON SCHEMA public TO sieve_probe; GRANT SELECT ON sieve_benchmark TO sieve_probe;",
                )
            old = json.loads(
                kubectl(
                    "create", "-f", "-", "-o", "json", payload=pooler_document(1, "A")
                )
            )
            old_uid = old["metadata"]["uid"]
            evidence["old_pooler"] = old

            def initial_children() -> dict[str, Resource] | None:
                deployment = get("deployment", POOLER)
                service = get("service", POOLER)
                if (
                    deployment is not None
                    and service is not None
                    and owned(deployment, old_uid)
                    and owned(service, old_uid)
                    and (deployment or {}).get("status", {}).get("readyReplicas") == 1
                ):
                    return {"deployment": deployment, "service": service}
                return None

            evidence["old_children"] = wait(initial_children, "initial owned children")
            if clients:
                clients.start()
                wait(
                    lambda: (
                        (get("pod", "sieve-client") or {})
                        .get("status", {})
                        .get("phase")
                        == "Running"
                    ),
                    "client pod running",
                )
                clients.follow()
                wait(clients.warm, "successful queries through both routes")
                clients.mark("clients_warm")
            evidence["armed_old_pooler"] = phase_patch(old_uid, "B")
            save()
            time.sleep(args.replacement_delay)
            if clients:
                clients.mark("delete_requested")
            response = json.loads(
                kubectl(
                    "delete",
                    "--raw",
                    f"/apis/postgresql.cnpg.io/v1/namespaces/default/poolers/{POOLER}",
                    "-f",
                    "-",
                    payload={
                        "apiVersion": "v1",
                        "kind": "DeleteOptions",
                        "propagationPolicy": "Background",
                        "preconditions": {"uid": old_uid},
                    },
                )
            )
            if (
                response.get("details", {}).get(
                    "uid", response.get("metadata", {}).get("uid")
                )
                != old_uid
            ):
                raise RuntimeError("Delete receipt does not identify old Pooler UID")
            if clients:
                clients.mark("delete_accepted")
            evidence["delete_response"] = response
            save()
            wait(
                lambda: get("poolers.postgresql.cnpg.io", POOLER) is None,
                "old Pooler deletion",
            )
            new = json.loads(
                kubectl(
                    "create",
                    "-f",
                    "-",
                    "-o",
                    "json",
                    payload=pooler_document(2, "replacement"),
                )
            )
            if new["metadata"]["uid"] == old_uid:
                raise RuntimeError("Replacement did not receive a new UID")
            evidence["new_pooler"] = new
            save()
            evidence["released_new_pooler"] = phase_patch(new["metadata"]["uid"], "C")
            if clients:
                clients.mark("replacement_C_accepted")
            save()

            def sample() -> bool:
                owner = get("poolers.postgresql.cnpg.io", POOLER)
                deployment, service = get("deployment", POOLER), get("service", POOLER)
                desired = bool(
                    owner
                    and owner["metadata"]["uid"] == new["metadata"]["uid"]
                    and owner["spec"] == new["spec"]
                    and not owner["metadata"].get("deletionTimestamp")
                )
                children_owned = owned(deployment, new["metadata"]["uid"]) and owned(
                    service, new["metadata"]["uid"]
                )
                ready = bool(
                    deployment
                    and deployment["spec"].get("replicas") == 2
                    and deployment.get("status", {}).get("readyReplicas") == 2
                    and deployment.get("status", {}).get("observedGeneration", 0)
                    >= deployment["metadata"]["generation"]
                )
                evidence["samples"].append(
                    {
                        "pooler": owner,
                        "deployment": deployment,
                        "service": service,
                        "desired": desired,
                        "children_owned": children_owned,
                        "ready": ready,
                    }
                )
                save()
                return desired and children_owned and ready

            converged = False
            try:
                wait(sample, "replacement convergence")
                converged = True
            except RuntimeError as error:
                if str(error) != "Timed out: replacement convergence":
                    raise
                evidence["convergence_timeout"] = str(error)
            if clients:
                clients.mark("converged")
            stable = converged
            end = time.monotonic() + args.observe_seconds
            while time.monotonic() < end:
                stable = sample() and stable
                time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
            if clients:
                clients.mark("observation_complete")
                evidence["clients"] = clients.finish()
                checks.extend(
                    [
                        {
                            "name": "continuous_client_evidence_complete",
                            "passed": all(
                                m["complete"] for m in evidence["clients"].values()
                            ),
                        },
                        {
                            "name": "client_query_results_correct",
                            "passed": all(
                                m["bad_results"] == 0
                                for m in evidence["clients"].values()
                            ),
                        },
                        {
                            "name": "both_client_routes_recovered",
                            "passed": all(
                                m["recovered"] for m in evidence["clients"].values()
                            ),
                        },
                    ]
                )
            final = evidence["samples"][-1]
            pods = json.loads(
                kubectl("get", "pods", "-l", "cnpg.io/cluster=" + CLUSTER, "-o", "json")
            )["items"]
            pods = [
                pod
                for pod in pods
                if any(
                    owner.get("uid") == cluster["metadata"]["uid"]
                    and owner.get("kind") == "Cluster"
                    and owner.get("controller") is True
                    for owner in pod["metadata"].get("ownerReferences", [])
                )
            ]
            evidence["database_pods"] = pods
            rows = {
                p["metadata"]["name"]: json.loads(sql(p["metadata"]["name"], query))
                for p in pods
            }
            roles = {
                p["metadata"]["name"]: sql(
                    p["metadata"]["name"], "SELECT pg_is_in_recovery();"
                )
                for p in pods
            }
            evidence.update(
                database_rows=rows,
                database_roles=roles,
                final_cluster=get("clusters.postgresql.cnpg.io", CLUSTER),
            )
            final_cluster = evidence["final_cluster"]
            checks.extend(
                [
                    {
                        "name": "pooler_identity_replaced",
                        "passed": new["metadata"]["uid"] != old_uid,
                    },
                    {
                        "name": "replacement_spec_identity_preserved",
                        "passed": final["desired"],
                    },
                    {
                        "name": "replacement_owns_children",
                        "passed": final["children_owned"],
                    },
                    {
                        "name": "replacement_deployment_converged",
                        "passed": converged and final["ready"],
                    },
                    {"name": "replacement_stable_after_convergence", "passed": stable},
                    {
                        "name": "cluster_spec_identity_unchanged",
                        "passed": bool(
                            final_cluster
                            and final_cluster["spec"] == cluster["spec"]
                            and all(
                                final_cluster["metadata"][k] == cluster["metadata"][k]
                                for k in ("uid", "generation")
                            )
                        ),
                    },
                    {
                        "name": "acknowledged_sql_rows_preserved",
                        "passed": len(rows) == 3
                        and all(r == EXPECTED_ROWS for r in rows.values()),
                    },
                    {
                        "name": "one_writable_primary",
                        "passed": len(roles) == 3
                        and list(roles.values()).count("f") == 1
                        and list(roles.values()).count("t") == 2,
                    },
                ]
            )
            success = all(check["passed"] for check in checks)
            evidence["status"] = "passed" if success else "semantic_failure"
            save()
            args.output.write_text(
                "FINISH-SIEVE-TEST\n"
                if success
                else "Pooler replacement invariant failed\n"
            )
            return 0 if success else 1
        except Exception as error:
            evidence.update(status="harness_or_setup_failure", error=str(error))
            save()
            args.output.write_text(str(error) + "\n")
            return 1
