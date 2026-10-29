"""Observe Pooler cleanup and database invariants across independent updates."""

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from sieve_tool.paths import repository_root
from sieve_tool.process import ManagedProcess, execution
from sieve_tool.testing.child_repair import Resource, get, kubectl

CLUSTER = "sieve-db"
POOLER = "sieve-pooler"
FINALIZER = "sieve.testing/foreground-cleanup"
PHASE = "sieve.testing/phase"
EXPECTED_ROWS = [[i, f"ack-{i}"] for i in range(1, 33)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["pooler-finalizer-updates"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--update-interval", type=float, default=8)
    parser.add_argument("--observe-seconds", type=float, default=45)
    parser.add_argument("--poll-interval", type=float, default=1)
    parser.add_argument("--ready-timeout", type=float, default=300)
    parser.add_argument(
        "--postgres-image",
        default=os.environ.get(
            "SIEVE_CNPG_POSTGRES_IMAGE",
            "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie",
        ),
    )
    parser.add_argument(
        "--pgbouncer-image",
        default=os.environ.get(
            "SIEVE_CNPG_PGBOUNCER_IMAGE", "ghcr.io/cloudnative-pg/pgbouncer:1.25.1"
        ),
    )
    args = parser.parse_args(argv)
    if (
        "@" in args.postgres_image
        and ":" not in args.postgres_image.partition("@")[0].rsplit("/", 1)[-1]
    ):
        parser.error(
            "PostgreSQL digest pins must retain the version tag before @sha256"
        )
    if any(
        not math.isfinite(v) or v <= 0
        for v in (
            args.update_interval,
            args.observe_seconds,
            args.poll_interval,
            args.ready_timeout,
        )
    ):
        parser.error("all time limits must be finite and positive")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "status": "starting",
        "recreated_children": [],
        "deletions": [],
        "observe_seconds": args.observe_seconds,
    }
    source: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "workload": args.workload,
        "resource_key": f"pooler/default/{POOLER}",
        "updates": [],
    }
    checks: list[dict[str, Any]] = []

    def save() -> None:
        (directory / "benchmark-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        (directory / "workload-updates.json").write_text(
            json.dumps(source, indent=2) + "\n"
        )
        if checks:
            (directory / "semantic-outcome.json").write_text(
                json.dumps({"schema_version": 1, "checks": checks}, indent=2) + "\n"
            )

    def wait[T](predicate: Callable[[], T | None], description: str) -> T:
        end = time.monotonic() + args.ready_timeout
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(args.poll_interval)
        raise RuntimeError("Timed out: " + description)

    def apply(document: Resource) -> Resource:
        response: Resource = json.loads(
            kubectl("apply", "-f", "-", "-o", "json", payload=document)
        )
        return response

    def delete(resource: Resource, path: str) -> Resource:
        response: Resource = json.loads(
            kubectl(
                "delete",
                "--raw",
                path,
                "-f",
                "-",
                payload={
                    "apiVersion": "v1",
                    "kind": "DeleteOptions",
                    "propagationPolicy": "Background",
                    "preconditions": {"uid": resource["metadata"]["uid"]},
                },
            )
        )
        uid = response.get("details", {}).get(
            "uid", response.get("metadata", {}).get("uid")
        )
        if uid != resource["metadata"]["uid"]:
            raise RuntimeError(
                "delete response does not identify the UID-tested mutation"
            )
        evidence["deletions"].append({"resource": resource, "response": response})
        return response
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
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-Atc",
            statement,
        ).strip()

    def cluster_ready() -> Resource | None:
        cluster = get("clusters.postgresql.cnpg.io", CLUSTER)
        status = (cluster or {}).get("status", {})
        return (
            cluster
            if status.get("readyInstances") == 3
            and status.get("currentPrimary")
            and status.get("currentPrimary") == status.get("targetPrimary")
            else None
        )

    with execution(log_dir=directory / "workload-commands"), ExitStack() as followers:
        try:
            apply(
                {
                    "apiVersion": "postgresql.cnpg.io/v1",
                    "kind": "Cluster",
                    "metadata": {"name": CLUSTER},
                    "spec": {
                        "instances": 3,
                        "imageName": args.postgres_image,
                        "storage": {"size": "1Gi"},
                        "resources": {"requests": {"memory": "256Mi", "cpu": "100m"}},
                    },
                }
            )
            cluster = wait(cluster_ready, "three ready PostgreSQL instances")
            evidence["cluster_before"] = cluster
            primary = cluster["status"]["currentPrimary"]
            sql(
                primary,
                "CREATE TABLE sieve_benchmark(id integer PRIMARY KEY,payload text NOT NULL); INSERT INTO sieve_benchmark SELECT i,'ack-'||i FROM generate_series(1,32) AS i;",
            )
            query = "SELECT json_agg(json_build_array(id,payload) ORDER BY id)::text FROM sieve_benchmark;"
            evidence["acknowledged_rows"] = json.loads(sql(primary, query))
            if evidence["acknowledged_rows"] != EXPECTED_ROWS:
                raise RuntimeError(
                    "initial acknowledged rows differ from declared data"
                )
            apply(
                {
                    "apiVersion": "postgresql.cnpg.io/v1",
                    "kind": "Pooler",
                    "metadata": {"name": POOLER},
                    "spec": {
                        "cluster": {"name": CLUSTER},
                        "instances": 1,
                        "type": "rw",
                        "pgbouncer": {
                            "poolMode": "transaction",
                            "image": args.pgbouncer_image,
                        },
                    },
                }
            )

            def deployment_ready() -> Resource | None:
                obj = get("deployment", POOLER)
                return (
                    obj
                    if obj and obj.get("status", {}).get("readyReplicas") == 1
                    else None
                )

            original = wait(deployment_ready, "ready Pooler Deployment")
            owner = get("poolers.postgresql.cnpg.io", POOLER)
            if not owner:
                raise RuntimeError("Pooler disappeared")
            evidence["owner_before"] = owner
            expected_owner = owner
            for kind in ("deployment", "service"):
                child = get(kind, POOLER)
                if not child or not any(
                    r.get("uid") == owner["metadata"]["uid"]
                    and r.get("kind") == "Pooler"
                    and r.get("controller") is True
                    for r in child["metadata"].get("ownerReferences", [])
                ):
                    raise RuntimeError("Pooler child ownership is unproven")
            # Positive control: an active owner still repairs a missing Deployment.
            delete(original, f"/apis/apps/v1/namespaces/default/deployments/{POOLER}")
            repaired = wait(
                lambda: (
                    obj
                    if (obj := deployment_ready())
                    and obj["metadata"]["uid"] != original["metadata"]["uid"]
                    else None
                ),
                "active Pooler repairs its Deployment",
            )
            evidence["active_parent_repair"] = {
                "old_uid": original["metadata"]["uid"],
                "replacement": repaired,
            }
            watches: list[tuple[str, Path, ManagedProcess]] = []
            watch_initial: dict[str, str] = {}
            for kind in ("deployment", "service"):
                child = get(kind, POOLER)
                if child is None:
                    raise RuntimeError("child missing before continuous observation")
                watch_initial[kind] = child["metadata"]["uid"]
                path = directory / f"owned-child-watch-{kind}.json-stream"
                process = followers.enter_context(
                    ManagedProcess(
                        [
                            "kubectl",
                            "--namespace",
                            "default",
                            "get",
                            kind,
                            POOLER,
                            "--watch",
                            "--output-watch-events",
                            "--request-timeout=900s",
                            "-o",
                            "json",
                        ],
                        operation="watch Pooler owned " + kind,
                        category="workload",
                        timeout=900,
                        cwd=repository_root(),
                        output=path,
                    )
                )
                watches.append((kind, path, process))

            def watch_events(*, complete: bool = False) -> list[dict[str, str]]:
                records = []
                for kind, path, _ in watches:
                    content = path.read_text()
                    decoder = json.JSONDecoder()
                    while content.strip():
                        content = content.lstrip()
                        try:
                            event, end = decoder.raw_decode(content)
                        except json.JSONDecodeError as error:
                            if complete:
                                raise RuntimeError(
                                    "incomplete owned-resource watch event"
                                ) from error
                            break
                        content = content[end:]
                        if not isinstance(event, dict) or not isinstance(
                            event.get("object"), dict
                        ):
                            raise RuntimeError("invalid owned-resource watch envelope")
                        obj = event["object"]
                        metadata = obj.get("metadata", {})
                        owners = metadata.get("ownerReferences", [])
                        owner_uid = owners[0].get("uid") if owners else None
                        uid = metadata.get("uid")
                        if (
                            event.get("type") not in ("ADDED", "MODIFIED", "DELETED")
                            or obj.get("kind", "").lower() != kind
                            or not isinstance(uid, str)
                            or not uid
                            or not isinstance(owner_uid, str)
                            or not owner_uid
                        ):
                            raise RuntimeError(
                                "invalid or incomplete owned-resource watch event"
                            )
                        records.append(
                            dict(
                                event=event["type"],
                                kind=kind,
                                uid=uid,
                                owner_uid=owner_uid,
                            )
                        )
                return records

            def watch_ready() -> bool:
                if any(
                    p.proc is None or p.proc.poll() is not None for _, _, p in watches
                ):
                    raise RuntimeError(
                        "owned-resource watch exited before observing deletion"
                    )
                records = watch_events()
                return all(
                    any(
                        r["kind"] == kind
                        and r["uid"] == uid
                        and r["owner_uid"] == owner["metadata"]["uid"]
                        for r in records
                    )
                    for kind, uid in watch_initial.items()
                )

            wait(watch_ready, "initial owned-resource watch receipts")

            def update(phase: str, *, add_finalizer: bool = False) -> None:
                current = get("poolers.postgresql.cnpg.io", POOLER)
                if (
                    not current
                    or current["metadata"]["uid"] != owner["metadata"]["uid"]
                ):
                    raise RuntimeError("Pooler identity changed")
                annotations = dict(current["metadata"].get("annotations", {}))
                annotations[PHASE] = phase
                operations = [
                    {
                        "op": "test",
                        "path": "/metadata/uid",
                        "value": owner["metadata"]["uid"],
                    },
                    {
                        "op": "add",
                        "path": "/metadata/annotations",
                        "value": annotations,
                    },
                ]
                if add_finalizer:
                    operations.append(
                        {
                            "op": "add",
                            "path": "/metadata/finalizers",
                            "value": [
                                *current["metadata"].get("finalizers", []),
                                FINALIZER,
                            ],
                        }
                    )
                response = json.loads(
                    kubectl(
                        "patch",
                        "poolers.postgresql.cnpg.io",
                        POOLER,
                        "--type=json",
                        "-p",
                        json.dumps(operations),
                        "-o",
                        "json",
                    )
                )
                if (
                    response["spec"] != expected_owner["spec"]
                    or any(
                        response["metadata"][k] != expected_owner["metadata"][k]
                        for k in ("uid", "generation")
                    )
                    or response["metadata"]["annotations"][PHASE] != phase
                ):
                    raise RuntimeError("update changed undeclared Pooler state")
                source["updates"].append(
                    {
                        "producer": "workload",
                        "state": {"metadata": {"annotations": {PHASE: phase}}},
                        "response": response,
                    }
                )
                save()

            update("A")
            time.sleep(args.update_interval)
            update("B", add_finalizer=True)
            time.sleep(args.update_interval)
            live = get("poolers.postgresql.cnpg.io", POOLER)
            if (
                live is None
                or live["spec"] != expected_owner["spec"]
                or any(
                    live["metadata"][k] != expected_owner["metadata"][k]
                    for k in ("uid", "generation")
                )
            ):
                raise RuntimeError("Pooler declared state changed before delete")
            expected_owner = delete(
                live, f"/apis/postgresql.cnpg.io/v1/namespaces/default/poolers/{POOLER}"
            )
            if (
                expected_owner.get("spec") != owner["spec"]
                or expected_owner.get("metadata", {}).get("uid")
                != owner["metadata"]["uid"]
                or type(expected_owner["metadata"].get("generation")) is not int
                or expected_owner["metadata"]["generation"]
                not in (
                    owner["metadata"]["generation"],
                    owner["metadata"]["generation"] + 1,
                )
                or not expected_owner["metadata"].get("deletionTimestamp")
            ):
                raise RuntimeError(
                    "owner deletion response does not identify its declared state"
                )
            evidence["declared_owner_after_delete"] = expected_owner
            deleting = get("poolers.postgresql.cnpg.io", POOLER)
            if (
                not deleting
                or deleting["spec"] != expected_owner["spec"]
                or any(
                    deleting["metadata"][k] != expected_owner["metadata"][k]
                    for k in ("uid", "generation", "deletionTimestamp")
                )
            ):
                raise RuntimeError("owner deletion did not enter finalization")
            evidence["owner_deleting"] = deleting
            old_children = {}
            for kind, prefix in (
                ("deployment", "/apis/apps/v1/namespaces/default/deployments"),
                ("service", "/api/v1/namespaces/default/services"),
            ):
                child = get(kind, POOLER)
                if not child:
                    raise RuntimeError(
                        "cleanup child disappeared before UID-tested deletion"
                    )
                old_children[kind] = child["metadata"]["uid"]
                delete(child, f"{prefix}/{POOLER}")
            update("C")
            time.sleep(args.update_interval)
            update("D")
            source["status"] = "completed"
            save()
            end = time.monotonic() + args.observe_seconds
            while time.monotonic() < end:
                watch_ready()
                for kind in old_children:
                    child = get(kind, POOLER)
                    if (
                        child
                        and child["metadata"]["uid"] != old_children[kind]
                        and child["metadata"]["uid"]
                        not in {
                            c["metadata"]["uid"] for c in evidence["recreated_children"]
                        }
                    ):
                        evidence["recreated_children"].append(child)
                time.sleep(args.poll_interval)
            final_cluster = wait(
                cluster_ready, "database remains ready after Pooler cleanup"
            )
            final_owner = get("poolers.postgresql.cnpg.io", POOLER)
            evidence.update(cluster_after=final_cluster, owner_after=final_owner)
            pods = json.loads(
                kubectl("get", "pods", "-l", f"cnpg.io/cluster={CLUSTER}", "-o", "json")
            )["items"]
            pods = [
                p
                for p in pods
                if any(
                    r.get("uid") == cluster["metadata"]["uid"]
                    and r.get("kind") == "Cluster"
                    for r in p["metadata"].get("ownerReferences", [])
                )
            ]
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
            evidence.update(database_rows=rows, database_roles=roles)
            watch_ready()
            followers.close()
            events = watch_events(complete=True)
            evidence["owned_child_events"] = events
            if any(
                not any(
                    r["kind"] == kind
                    and r["uid"] == uid
                    and r["event"] == "DELETED"
                    and r["owner_uid"] == owner["metadata"]["uid"]
                    for r in events
                )
                for kind, uid in old_children.items()
            ):
                raise RuntimeError(
                    "owned-resource watch did not acknowledge child deletion"
                )
            evidence["recreated_child_events"] = [
                r
                for r in events
                if r["owner_uid"] == owner["metadata"]["uid"]
                and r["uid"] != watch_initial[r["kind"]]
            ]
            checks.extend(
                [
                    {
                        "name": "active_owner_repairs_missing_child",
                        "passed": repaired["metadata"]["uid"]
                        != original["metadata"]["uid"],
                    },
                    {
                        "name": "no_children_recreated_after_owner_deletion",
                        "passed": not evidence["recreated_children"]
                        and not evidence["recreated_child_events"],
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
                    {
                        "name": "cluster_spec_identity_unchanged",
                        "passed": final_cluster["spec"] == cluster["spec"]
                        and all(
                            final_cluster["metadata"][k] == cluster["metadata"][k]
                            for k in ("uid", "generation")
                        ),
                    },
                    {
                        "name": "pooler_spec_identity_matches_declared_deletion",
                        "passed": final_owner is not None
                        and final_owner["spec"] == owner["spec"]
                        and all(
                            final_owner["metadata"][k] == expected_owner["metadata"][k]
                            for k in ("uid", "generation")
                        )
                        and bool(final_owner["metadata"].get("deletionTimestamp")),
                    },
                ]
            )
            evidence["status"] = (
                "passed" if all(c["passed"] for c in checks) else "semantic_failure"
            )
            save()
            success = all(c["passed"] for c in checks)
            args.output.write_text(
                "FINISH-SIEVE-TEST\n"
                if success
                else "CloudNativePG application invariant failed; inspect semantic-outcome.json\n"
            )
            return 0 if success else 1
        except Exception as error:
            evidence.update(status="harness_or_setup_failure", error=str(error))
            save()
            args.output.write_text(str(error) + "\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
