"""Observe native certificate bootstrap, including transient operator failures."""

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Any

from sieve_tool.process import deadline, execution
from sieve_tool.testing.child_repair import Resource, get, kubectl, owned_by

CLUSTER = "sieve-ca-bootstrap"
EXPECTED_ROWS = [[i, f"ack-{i}"] for i in range(1, 33)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["ca-secret-bootstrap"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--ready-timeout", type=float, default=300)
    parser.add_argument("--observe-seconds", type=float, default=30)
    parser.add_argument("--poll-interval", type=float, default=1)
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
        for v in (args.ready_timeout, args.observe_seconds, args.poll_interval)
    ):
        parser.error("all time limits must be finite and positive")
    if (
        "@" in args.postgres_image
        and ":" not in args.postgres_image.partition("@")[0].rsplit("/", 1)[-1]
    ):
        parser.error("PostgreSQL digest pins must retain the version tag")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "status": "starting",
        "cluster_samples": [],
        "auxiliary_object_errors": [],
        "database_rows": {},
        "database_recovery": {},
    }
    checks: list[dict[str, Any]] = []

    def save() -> None:
        (directory / "bootstrap-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        (directory / "semantic-outcome.json").write_text(
            json.dumps({"schema_version": 1, "checks": checks}, indent=2) + "\n"
        )

    def check(name: str, passed: bool) -> None:
        checks.append({"name": name, "passed": passed})
        save()

    def sample() -> Resource | None:
        cluster = get("clusters.postgresql.cnpg.io", CLUSTER)
        evidence["cluster_samples"].append(
            {"monotonic_ns": time.monotonic_ns(), "cluster": cluster}
        )
        if cluster is not None:
            initial = evidence["accepted_cluster"]
            if (
                cluster["metadata"]["uid"] != initial["metadata"]["uid"]
                or cluster["metadata"].get("generation")
                != initial["metadata"].get("generation")
                or cluster["spec"] != initial["spec"]
                or cluster["metadata"].get("deletionTimestamp")
            ):
                raise RuntimeError("Cluster identity/spec changed during bootstrap")
            status = cluster.get("status", {})
            if status.get("phase") == "Unable to create required cluster objects":
                evidence["auxiliary_object_errors"].append(
                    {"monotonic_ns": time.monotonic_ns(), "status": status}
                )
        save()
        return cluster

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

    with execution(log_dir=directory / "workload-commands"):
        try:
            with deadline(args.ready_timeout):
                evidence["accepted_cluster"] = json.loads(
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
                                "imageName": args.postgres_image,
                                "storage": {"size": "1Gi"},
                                "resources": {
                                    "requests": {"memory": "256Mi", "cpu": "100m"}
                                },
                            },
                        },
                    )
                )
                save()
                end = time.monotonic() + args.ready_timeout
                pods: list[Resource] = []
                while time.monotonic() < end:
                    cluster = sample()
                    status = (cluster or {}).get("status", {})
                    if (
                        cluster is not None
                        and status.get("readyInstances") == 3
                        and status.get("currentPrimary") == status.get("targetPrimary")
                        and status.get("currentPrimary")
                    ):
                        pods = json.loads(
                            kubectl(
                                "get",
                                "pods",
                                "-l",
                                "cnpg.io/cluster=" + CLUSTER,
                                "-o",
                                "json",
                            )
                        )["items"]
                        if len(pods) == 3 and all(
                            owned_by(p, cluster)
                            and not p["metadata"].get("deletionTimestamp")
                            and any(
                                c.get("type") == "Ready" and c.get("status") == "True"
                                for c in p.get("status", {}).get("conditions", [])
                            )
                            for p in pods
                        ):
                            break
                    time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
                else:
                    raise RuntimeError(
                        "Timed out: three ready owned database instances"
                    )
                assert cluster is not None
                evidence["ready_cluster"] = cluster
                evidence["database_pods"] = pods
                check("three_ready_owned_instances", True)
                for suffix in ("ca", "server", "replication"):
                    secret = get("secret", CLUSTER + "-" + suffix)
                    if not secret or not owned_by(secret, cluster):
                        raise RuntimeError("Missing or foreign PKI Secret: " + suffix)
                    evidence.setdefault("PKI_secrets", {})[suffix] = secret
                check("owned_PKI_secrets", True)
                primary = cluster["status"]["currentPrimary"]
                sql(
                    primary,
                    "CREATE TABLE sieve_bootstrap(id integer PRIMARY KEY,payload text NOT NULL); INSERT INTO sieve_bootstrap SELECT i,'ack-'||i FROM generate_series(1,32) AS i;",
                )
                query = "SELECT json_agg(json_build_array(id,payload) ORDER BY id)::text FROM sieve_bootstrap;"
                while time.monotonic() < end:
                    for pod in pods:
                        name = pod["metadata"]["name"]
                        evidence["database_rows"][name] = json.loads(sql(name, query))
                        evidence["database_recovery"][name] = sql(
                            name, "SELECT pg_is_in_recovery();"
                        )
                    save()
                    if all(
                        rows == EXPECTED_ROWS
                        for rows in evidence["database_rows"].values()
                    ):
                        break
                    time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
                check(
                    "acknowledged_32_rows_on_three_instances",
                    len(evidence["database_rows"]) == 3
                    and all(
                        rows == EXPECTED_ROWS
                        for rows in evidence["database_rows"].values()
                    ),
                )
                check(
                    "exactly_one_writable_primary",
                    evidence["database_recovery"].get(primary) == "f"
                    and list(evidence["database_recovery"].values()).count("f") == 1
                    and list(evidence["database_recovery"].values()).count("t") == 2,
                )
            end = time.monotonic() + args.observe_seconds
            stable = True
            with deadline(args.observe_seconds + args.ready_timeout):
                while time.monotonic() < end:
                    current = sample()
                    status = (current or {}).get("status", {})
                    stable = stable and (
                        status.get("readyInstances") == 3
                        and status.get("currentPrimary") == primary
                        and status.get("targetPrimary") == primary
                    )
                    time.sleep(min(args.poll_interval, max(0, end - time.monotonic())))
            check("bootstrap_stays_ready", stable)
            check(
                "no_auxiliary_object_bootstrap_errors",
                not evidence["auxiliary_object_errors"],
            )
            evidence["status"] = (
                "passed" if all(c["passed"] for c in checks) else "semantic_failure"
            )
            args.output.write_text("FINISH-SIEVE-TEST\n")
            save()
            return 0 if evidence["status"] == "passed" else 1
        except Exception as error:
            evidence.update(status="incomplete", error=str(error))
            check("bootstrap_completed", False)
            args.output.write_text("ERROR: " + str(error) + "\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
