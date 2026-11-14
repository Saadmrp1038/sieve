#!/usr/bin/env python3
"""Compare KEDA replica restoration before and after a scale-target handoff."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sieve_tool.process import Result, execution
from sieve_tool.process import run as run_process

NAMESPACE = "default"
SCALED_OBJECT = "sieve-target-handoff"
HPA = "keda-hpa-" + SCALED_OBJECT
TARGET_A = "sieve-scale-a"
TARGET_B = "sieve-scale-b"
MONITOR = "sieve-scale-monitor"
INITIAL_REPLICAS = {TARGET_A: 4, TARGET_B: 1}


class WorkloadError(RuntimeError):
    """The workload could not establish its required evidence."""


class Recorder:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.observations: list[dict[str, Any]] = []

    def kubectl(
        self,
        *args: str,
        stdin: dict[str, Any] | None = None,
        check: bool = True,
    ) -> Result:
        result = run_process(
            ["kubectl", *args],
            operation="kubectl " + " ".join(args[:3]),
            input=None if stdin is None else json.dumps(stdin),
            capture_output=True,
            check=False,
            timeout=60,
            cwd=self.output.parent,
        )
        if check and result.returncode:
            raise WorkloadError(
                f"kubectl {' '.join(args)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result

    def object(self, kind: str, name: str, *, namespaced: bool = True) -> Any:
        args = ["get", kind, name]
        if namespaced:
            args.extend(["-n", NAMESPACE])
        args.extend(["--ignore-not-found", "-o", "json"])
        result = self.kubectl(*args)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def apply(self, manifest: dict[str, Any]) -> dict[str, Any]:
        result = self.kubectl(
            "apply", "-f", "-", "-n", NAMESPACE, "-o", "json", stdin=manifest
        )
        return json.loads(result.stdout)

    def observe(self, phase: str) -> dict[str, Any]:
        pods = json.loads(
            self.kubectl(
                "get", "pods", "-n", NAMESPACE, "-l", "sievetag=keda", "-o", "json"
            ).stdout
        ).get("items", [])
        sample = {
            "phase": phase,
            "time": datetime.now(timezone.utc).isoformat(),
            "target_a": self.object("deployment", TARGET_A),
            "target_b": self.object("deployment", TARGET_B),
            "scaled_object": self.object("scaledobject", SCALED_OBJECT),
            "hpa": self.object("horizontalpodautoscaler", HPA),
            "controller_pods": pods,
        }
        self.observations.append(sample)
        return sample


def deployment(name: str, replicas: int, *, monitored: bool = False) -> dict[str, Any]:
    labels = {"app": MONITOR if monitored else name}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [
                        {
                            "name": "pause",
                            "image": "registry.k8s.io/pause:3.10.1",
                            "resources": {
                                "requests": {"cpu": "1m", "memory": "1Mi"},
                                "limits": {"cpu": "10m", "memory": "8Mi"},
                            },
                        }
                    ]
                },
            },
        },
    }


def scaled_object(target: str) -> dict[str, Any]:
    return {
        "apiVersion": "keda.sh/v1alpha1",
        "kind": "ScaledObject",
        "metadata": {"name": SCALED_OBJECT, "namespace": NAMESPACE},
        "spec": {
            "scaleTargetRef": {"name": target},
            "pollingInterval": 2,
            "cooldownPeriod": 0,
            "minReplicaCount": 2,
            "maxReplicaCount": 2,
            "advanced": {"restoreToOriginalReplicaCount": True},
            "triggers": [
                {
                    "type": "kubernetes-workload",
                    "metadata": {"podSelector": "app=" + MONITOR, "value": "1"},
                }
            ],
        },
    }


def replica_count(resource: dict[str, Any] | None) -> int | None:
    if resource is None:
        return None
    return resource.get("spec", {}).get("replicas")


def deployment_ready(resource: dict[str, Any] | None, replicas: int) -> bool:
    if resource is None:
        return False
    status = resource.get("status", {})
    return (
        replica_count(resource) == replicas
        and status.get("observedGeneration")
        == resource.get("metadata", {}).get("generation")
        and status.get("availableReplicas", 0) == replicas
    )


def scaled_object_ready(resource: dict[str, Any] | None) -> bool:
    if resource is None:
        return False
    return any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in resource.get("status", {}).get("conditions", [])
    )


def hpa_target(resource: dict[str, Any] | None) -> str | None:
    if resource is None:
        return None
    return resource.get("spec", {}).get("scaleTargetRef", {}).get("name")


def saved_original(resource: dict[str, Any] | None) -> int | None:
    if resource is None:
        return None
    return resource.get("status", {}).get("originalReplicaCount")


def wait_for(
    recorder: Recorder,
    phase: str,
    timeout: float,
    interval: float,
    settle: float,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    stable_since: float | None = None
    last: dict[str, Any] | None = None
    while True:
        last = recorder.observe(phase)
        now = time.monotonic()
        if predicate(last):
            stable_since = stable_since or now
            if now - stable_since >= settle:
                return last
        else:
            stable_since = None
        if now >= deadline:
            raise WorkloadError(
                f"timed out waiting for {phase}; last sample: "
                + json.dumps(last, sort_keys=True)
            )
        time.sleep(interval)


def worker_count(deployment_resource: dict[str, Any]) -> str | None:
    containers = deployment_resource["spec"]["template"]["spec"]["containers"]
    operator = next(
        (item for item in containers if item.get("name") == "keda-operator"), None
    )
    if operator is None:
        return None
    return next(
        (
            item.get("value")
            for item in operator.get("env", [])
            if item.get("name") == "KEDA_SCALEDOBJECT_CTRL_MAX_RECONCILES"
        ),
        None,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.workload not in {
        "restore-control-a",
        "restore-control-b",
        "target-handoff",
    }:
        raise WorkloadError(f"unsupported workload {args.workload!r}")
    recorder = Recorder(args.output)
    operator = recorder.object("deployment", "keda-operator")
    if operator is None:
        raise WorkloadError("keda-operator deployment is missing")
    crd = recorder.object(
        "customresourcedefinition", "scaledobjects.keda.sh", namespaced=False
    )
    if crd is None:
        raise WorkloadError("ScaledObject CRD is missing")
    if worker_count(operator) != "10":
        raise WorkloadError("experiment requires ten native ScaledObject workers")
    controller_pods = json.loads(
        recorder.kubectl(
            "get", "pods", "-n", NAMESPACE, "-l", "sievetag=keda", "-o", "json"
        ).stdout
    ).get("items", [])
    if len(controller_pods) != 1:
        raise WorkloadError(
            "experiment requires exactly one identified KEDA operator Pod"
        )
    if any(
        recorder.object(kind, name) is not None
        for kind, name in (
            ("deployment", TARGET_A),
            ("deployment", TARGET_B),
            ("deployment", MONITOR),
            ("scaledobject", SCALED_OBJECT),
            ("horizontalpodautoscaler", HPA),
        )
    ):
        raise WorkloadError("synthetic KEDA resources already exist")

    direct_target = TARGET_B if args.workload == "restore-control-b" else TARGET_A
    current_target = TARGET_B if args.workload == "target-handoff" else direct_target
    evidence: dict[str, Any] = {
        "status": "running",
        "semantic_status": "not-evaluated",
        "workload": args.workload,
        "operator_deployment": operator,
        "controller_pods": controller_pods,
        "workers": 10,
        "accepted": [],
    }
    evidence_path = args.output.with_name("target-handoff.json")

    def save() -> None:
        evidence["observations"] = recorder.observations
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    def accept(resource: dict[str, Any]) -> dict[str, Any]:
        evidence["accepted"].append(
            {"time": datetime.now(timezone.utc).isoformat(), "resource": resource}
        )
        save()
        return resource

    try:
        accept(recorder.apply(deployment(TARGET_A, INITIAL_REPLICAS[TARGET_A])))
        accept(recorder.apply(deployment(TARGET_B, INITIAL_REPLICAS[TARGET_B])))
        accept(recorder.apply(deployment(MONITOR, 1, monitored=True)))
        wait_for(
            recorder,
            "initial-deployments-ready",
            args.timeout,
            args.poll_seconds,
            args.settle_seconds,
            lambda sample: (
                deployment_ready(sample["target_a"], INITIAL_REPLICAS[TARGET_A])
                and deployment_ready(sample["target_b"], INITIAL_REPLICAS[TARGET_B])
            ),
        )
        accepted_scaled_object = accept(recorder.apply(scaled_object(direct_target)))
        initial_scaled = wait_for(
            recorder,
            "initial-target-scaled",
            args.timeout,
            args.poll_seconds,
            args.settle_seconds,
            lambda sample: (
                scaled_object_ready(sample["scaled_object"])
                and saved_original(sample["scaled_object"])
                == INITIAL_REPLICAS[direct_target]
                and hpa_target(sample["hpa"]) == direct_target
                and deployment_ready(
                    sample["target_a" if direct_target == TARGET_A else "target_b"], 2
                )
            ),
        )
        evidence["initial_scaled"] = {
            "target": direct_target,
            "saved_original_replica_count": saved_original(
                initial_scaled["scaled_object"]
            ),
            "target_replicas": replica_count(
                initial_scaled["target_a" if direct_target == TARGET_A else "target_b"]
            ),
            "hpa_target": hpa_target(initial_scaled["hpa"]),
        }

        if args.workload == "target-handoff":
            result = recorder.kubectl(
                "patch",
                "scaledobject",
                SCALED_OBJECT,
                "-n",
                NAMESPACE,
                "--type=merge",
                "-p",
                json.dumps({"spec": {"scaleTargetRef": {"name": TARGET_B}}}),
                "-o",
                "json",
            )
            accepted_target_update = json.loads(result.stdout)
            accept(accepted_target_update)
            evidence["accepted_target_update"] = accepted_target_update
            after_handoff = wait_for(
                recorder,
                "target-handoff-reconciled",
                args.timeout,
                args.poll_seconds,
                args.settle_seconds,
                lambda sample: (
                    sample["scaled_object"] is not None
                    and sample["scaled_object"]["spec"]["scaleTargetRef"]["name"]
                    == TARGET_B
                    and scaled_object_ready(sample["scaled_object"])
                    and hpa_target(sample["hpa"]) == TARGET_B
                    and deployment_ready(sample["target_b"], 2)
                ),
            )
            evidence["after_handoff"] = {
                "current_target": TARGET_B,
                "saved_original_replica_count": saved_original(
                    after_handoff["scaled_object"]
                ),
                "target_replicas": replica_count(after_handoff["target_b"]),
                "hpa_target": hpa_target(after_handoff["hpa"]),
            }
        else:
            evidence["accepted_target_update"] = accepted_scaled_object
            after_handoff = initial_scaled
            evidence["after_handoff"] = evidence["initial_scaled"]

        expected_original = INITIAL_REPLICAS[current_target]
        recorder.kubectl(
            "delete",
            "scaledobject",
            SCALED_OBJECT,
            "-n",
            NAMESPACE,
            "--wait=true",
            f"--timeout={args.timeout}s",
        )
        final_sample = wait_for(
            recorder,
            "post-deletion-restoration",
            args.timeout,
            args.poll_seconds,
            args.settle_seconds,
            lambda sample: (
                sample["scaled_object"] is None
                and sample["hpa"] is None
                and replica_count(
                    sample["target_a" if current_target == TARGET_A else "target_b"]
                )
                is not None
            ),
        )
        final_replicas = replica_count(
            final_sample["target_a" if current_target == TARGET_A else "target_b"]
        )
        final_controller_pods = final_sample["controller_pods"]
        checks = {
            "controller_uses_ten_workers": worker_count(operator) == "10",
            "controller_pod_identity_is_stable": (
                len(final_controller_pods) == 1
                and final_controller_pods[0].get("metadata", {}).get("uid")
                == controller_pods[0].get("metadata", {}).get("uid")
                and final_controller_pods[0]
                .get("status", {})
                .get("containerStatuses", [{}])[0]
                .get("restartCount")
                == controller_pods[0]
                .get("status", {})
                .get("containerStatuses", [{}])[0]
                .get("restartCount")
            ),
            "hpa_follows_current_target": (
                evidence["after_handoff"]["hpa_target"] == current_target
            ),
            "current_target_restored_to_its_original_count": (
                final_replicas == expected_original
            ),
        }
        evidence.update(
            {
                "status": "completed",
                "semantic_status": "passed" if all(checks.values()) else "failed",
                "current_target": current_target,
                "expected_original_replica_count": expected_original,
                "checks": checks,
                "final": {
                    "current_target": current_target,
                    "current_target_replicas": final_replicas,
                    "target_a_replicas": replica_count(final_sample["target_a"]),
                    "target_b_replicas": replica_count(final_sample["target_b"]),
                    "scaled_object": final_sample["scaled_object"],
                    "hpa": final_sample["hpa"],
                },
            }
        )
        save()
    finally:
        recorder.kubectl(
            "delete",
            "deployment",
            TARGET_A,
            TARGET_B,
            MONITOR,
            "-n",
            NAMESPACE,
            "--ignore-not-found",
            "--wait=true",
            check=False,
        )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload")
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    values = (args.timeout, args.poll_seconds, args.settle_seconds)
    if not all(math.isfinite(value) for value in values):
        parser.error("timeouts must be finite")
    if args.timeout <= 0 or args.poll_seconds <= 0 or args.settle_seconds < 0:
        parser.error(
            "timeouts must be positive and settle-seconds must be non-negative"
        )
    return args


def main() -> int:
    args = parse_args()
    evidence_path = args.output.with_name("target-handoff.json")
    try:
        with execution(log_dir=args.output.parent / "kubectl-commands"):
            run(args)
    except Exception as error:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        failed: dict[str, Any] = {
            "status": "harness-failed",
            "semantic_status": "not-evaluated",
            "error": str(error),
            "workload": args.workload,
        }
        if evidence_path.exists():
            try:
                failed = {**json.loads(evidence_path.read_text()), **failed}
            except json.JSONDecodeError:
                pass
        evidence_path.write_text(json.dumps(failed, indent=2, sort_keys=True) + "\n")
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
