#!/usr/bin/env python3
"""Exercise same-name Prometheus replacement with a retained StatefulSet."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

NAMESPACE = "default"
PARENT = "sieve-handoff"
STATEFULSET = "prometheus-sieve-handoff"
POD = f"{STATEFULSET}-0"
POLICY = "sieve-prometheus-handoff-delete"


class WorkloadError(RuntimeError):
    """The workload could not establish its required evidence."""


class Recorder:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.commands = output.with_name("kubectl-commands.jsonl")
        self.observations = output.with_name("ownership-observations.jsonl")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def append(self, path: Path, record: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def kubectl(
        self,
        *args: str,
        stdin: dict[str, Any] | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        result = subprocess.run(
            ["kubectl", *args],
            input=None if stdin is None else json.dumps(stdin),
            text=True,
            capture_output=True,
            timeout=60,
        )
        self.append(
            self.commands,
            {
                "args": list(args),
                "duration_seconds": time.monotonic() - started,
                "returncode": result.returncode,
                "stderr": result.stderr,
                "stdout": result.stdout,
                "time": self.now(),
            },
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

    def pods(self) -> list[dict[str, Any]]:
        result = self.kubectl(
            "get",
            "pods",
            "-n",
            NAMESPACE,
            "-l",
            f"operator.prometheus.io/name={PARENT}",
            "-o",
            "json",
        )
        return json.loads(result.stdout).get("items", [])

    def observe(self, phase: str) -> dict[str, Any]:
        parent = self.object("prometheus", PARENT)
        statefulset = self.object("statefulset", STATEFULSET)
        pods = self.pods()
        pod = next((item for item in pods if item["metadata"]["name"] == POD), None)
        sample = {
            "phase": phase,
            "time": self.now(),
            "parent": parent,
            "statefulset": statefulset,
            "pod": pod,
        }
        self.append(self.observations, sample)
        return sample


def metadata_uid(obj: dict[str, Any] | None) -> str | None:
    if obj is None:
        return None
    return obj.get("metadata", {}).get("uid")


def controller_owner_uid(
    obj: dict[str, Any] | None, *, kind: str, name: str
) -> str | None:
    if obj is None:
        return None
    for owner in obj.get("metadata", {}).get("ownerReferences", []):
        if (
            owner.get("kind") == kind
            and owner.get("name") == name
            and owner.get("controller") is True
        ):
            return owner.get("uid")
    return None


def condition_true(obj: dict[str, Any] | None, condition_type: str) -> bool:
    if obj is None:
        return False
    generation = obj.get("metadata", {}).get("generation")
    return any(
        condition.get("type") == condition_type
        and condition.get("status") == "True"
        and condition.get("observedGeneration", generation) == generation
        for condition in obj.get("status", {}).get("conditions", [])
    )


def statefulset_ready(obj: dict[str, Any] | None) -> bool:
    if obj is None or obj.get("metadata", {}).get("deletionTimestamp"):
        return False
    status = obj.get("status", {})
    return (
        status.get("observedGeneration") == obj.get("metadata", {}).get("generation")
        and status.get("readyReplicas", 0) >= 1
    )


def pod_ready(obj: dict[str, Any] | None) -> bool:
    if obj is None or obj.get("metadata", {}).get("deletionTimestamp"):
        return False
    return obj.get("status", {}).get("phase") == "Running" and any(
        status.get("ready") is True
        for status in obj.get("status", {}).get("containerStatuses", [])
        if status.get("name") == "prometheus"
    )


def acknowledged_available(sample: dict[str, Any]) -> bool:
    return (
        condition_true(sample["parent"], "Reconciled")
        and condition_true(sample["parent"], "Available")
        and statefulset_ready(sample["statefulset"])
        and pod_ready(sample["pod"])
    )


def wait_for(
    recorder: Recorder,
    phase: str,
    timeout: float,
    interval: float,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while True:
        last = recorder.observe(phase)
        if predicate(last):
            return last
        if time.monotonic() >= deadline:
            raise WorkloadError(
                f"timed out waiting for {phase}; last sample: "
                + json.dumps(last, sort_keys=True)
            )
        time.sleep(interval)


def stable_for(
    recorder: Recorder,
    phase: str,
    timeout: float,
    interval: float,
    duration: float,
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
            if now - stable_since >= duration:
                return last
        else:
            stable_since = None
        if now >= deadline:
            raise WorkloadError(
                f"timed out establishing stable {phase}; last sample: "
                + json.dumps(last, sort_keys=True)
            )
        time.sleep(interval)


def prometheus_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "Prometheus",
        "metadata": {"name": PARENT, "namespace": NAMESPACE},
        "spec": {
            "image": "quay.io/prometheus/prometheus@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0",
            "replicas": 1,
            "serviceMonitorSelector": {},
            "podMonitorSelector": {},
            "probeSelector": {},
            "scrapeConfigSelector": {},
            "ruleSelector": {},
            "retention": "15m",
        },
    }


def policy_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    policy = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": POLICY},
        "spec": {
            "failurePolicy": "Fail",
            "matchConstraints": {
                "resourceRules": [
                    {
                        "apiGroups": ["apps"],
                        "apiVersions": ["v1"],
                        "operations": ["DELETE"],
                        "resources": ["statefulsets"],
                        "scope": "Namespaced",
                    }
                ]
            },
            "validations": [
                {
                    "expression": (
                        "oldObject.metadata.namespace != 'default' || "
                        "oldObject.metadata.name != 'prometheus-sieve-handoff'"
                    ),
                    "message": "sieve synthetic StatefulSet delete denial",
                }
            ],
        },
    }
    binding = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicyBinding",
        "metadata": {"name": POLICY},
        "spec": {"policyName": POLICY, "validationActions": ["Deny"]},
    }
    return policy, binding


def compact(sample: dict[str, Any]) -> dict[str, Any]:
    parent = sample["parent"]
    statefulset = sample["statefulset"]
    pod = sample["pod"]
    return {
        "time": sample["time"],
        "parent_uid": metadata_uid(parent),
        "parent_generation": (parent or {}).get("metadata", {}).get("generation"),
        "reconciled": condition_true(parent, "Reconciled"),
        "available": condition_true(parent, "Available"),
        "statefulset_uid": metadata_uid(statefulset),
        "statefulset_owner_uid": controller_owner_uid(
            statefulset, kind="Prometheus", name=PARENT
        ),
        "statefulset_hash": (statefulset or {})
        .get("metadata", {})
        .get("annotations", {})
        .get("prometheus-operator-input-hash"),
        "statefulset_deletion_timestamp": (statefulset or {})
        .get("metadata", {})
        .get("deletionTimestamp"),
        "statefulset_ready": statefulset_ready(statefulset),
        "pod_uid": metadata_uid(pod),
        "pod_owner_uid": controller_owner_uid(
            pod, kind="StatefulSet", name=STATEFULSET
        ),
        "pod_ready": pod_ready(pod),
    }


def apply(recorder: Recorder, manifest: dict[str, Any]) -> dict[str, Any] | None:
    result = recorder.kubectl("apply", "-f", "-", "-o", "json", stdin=manifest)
    return json.loads(result.stdout) if result.stdout.strip() else None


def remove_policy(recorder: Recorder) -> None:
    recorder.kubectl(
        "delete",
        "validatingadmissionpolicybinding",
        POLICY,
        "--ignore-not-found",
    )
    recorder.kubectl(
        "delete", "validatingadmissionpolicy", POLICY, "--ignore-not-found"
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    mode = args.workload.removeprefix("owner-handoff-")
    if mode not in {"control", "rejected"}:
        raise WorkloadError(f"unsupported workload {args.workload!r}")

    recorder = Recorder(args.output)
    deployment = recorder.object("deployment", "prometheus-operator")
    if deployment is None:
        raise WorkloadError("prometheus-operator deployment is missing")
    crd = recorder.object(
        "customresourcedefinition",
        "prometheuses.monitoring.coreos.com",
        namespaced=False,
    )
    if crd is None:
        raise WorkloadError("Prometheus CRD is missing")

    evidence: dict[str, Any] = {
        "status": "running",
        "workload": args.workload,
        "operator_deployment": deployment,
        "policy_probe": None,
    }
    evidence_path = args.output.with_name("ownership-handoff.json")
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    apply(recorder, prometheus_manifest())
    initial_sample = stable_for(
        recorder,
        "initial",
        args.timeout,
        args.poll_seconds,
        args.settle_seconds,
        acknowledged_available,
    )
    initial = compact(initial_sample)
    if (
        initial["statefulset_owner_uid"] != initial["parent_uid"]
        or initial["pod_owner_uid"] != initial["statefulset_uid"]
    ):
        raise WorkloadError("initial child ownership is inconsistent")

    old_parent_uid = initial["parent_uid"]
    old_statefulset_uid = initial["statefulset_uid"]

    if mode == "rejected":
        policy, binding = policy_manifests()
        apply(recorder, policy)
        apply(recorder, binding)
        probe_deadline = time.monotonic() + args.timeout
        while True:
            probe = recorder.kubectl(
                "delete",
                "statefulset",
                STATEFULSET,
                "-n",
                NAMESPACE,
                "--dry-run=server",
                check=False,
            )
            denied = (
                probe.returncode != 0
                and "sieve synthetic StatefulSet delete denial" in probe.stderr
            )
            if denied:
                evidence["policy_probe"] = {
                    "accepted": False,
                    "returncode": probe.returncode,
                    "stderr": probe.stderr,
                }
                break
            if probe.returncode != 0:
                raise WorkloadError(
                    "StatefulSet dry-run failed for a reason other than the synthetic "
                    f"policy: {probe.stderr.strip()}"
                )
            if time.monotonic() >= probe_deadline:
                raise WorkloadError(
                    "synthetic StatefulSet delete policy did not activate"
                )
            time.sleep(args.poll_seconds)

    recorder.kubectl("delete", "prometheus", PARENT, "-n", NAMESPACE, "--wait=false")

    if mode == "control":
        wait_for(
            recorder,
            "old-parent-deleted-control",
            args.timeout,
            args.poll_seconds,
            lambda sample: (
                sample["parent"] is None
                and sample["statefulset"] is None
                and sample["pod"] is None
            ),
        )
    else:
        wait_for(
            recorder,
            "old-parent-deleted-retained-child",
            args.timeout,
            args.poll_seconds,
            lambda sample: (
                sample["parent"] is None
                and metadata_uid(sample["statefulset"]) == old_statefulset_uid
                and controller_owner_uid(
                    sample["statefulset"], kind="Prometheus", name=PARENT
                )
                == old_parent_uid
                and not sample["statefulset"]
                .get("metadata", {})
                .get("deletionTimestamp")
            ),
        )

    apply(recorder, prometheus_manifest())
    handoff_sample = stable_for(
        recorder,
        "replacement-acknowledged",
        args.timeout,
        args.poll_seconds,
        args.settle_seconds,
        lambda sample: (
            metadata_uid(sample["parent"]) not in {None, old_parent_uid}
            and acknowledged_available(sample)
        ),
    )
    handoff = compact(handoff_sample)

    current_owner = handoff["statefulset_owner_uid"] == handoff["parent_uid"]
    stale_status_accepted = handoff["available"] and not current_owner

    if mode == "rejected":
        remove_policy(recorder)

    final_sample = stable_for(
        recorder,
        "final-current-ownership",
        args.timeout,
        args.poll_seconds,
        args.settle_seconds,
        lambda sample: (
            acknowledged_available(sample)
            and metadata_uid(sample["parent"]) == handoff["parent_uid"]
            and controller_owner_uid(
                sample["statefulset"], kind="Prometheus", name=PARENT
            )
            == handoff["parent_uid"]
            and controller_owner_uid(
                sample["pod"], kind="StatefulSet", name=STATEFULSET
            )
            == metadata_uid(sample["statefulset"])
        ),
    )
    final = compact(final_sample)

    checks = {
        "initial_child_owned_by_initial_parent": (
            initial["statefulset_owner_uid"] == initial["parent_uid"]
            and initial["pod_owner_uid"] == initial["statefulset_uid"]
        ),
        "replacement_parent_has_new_uid": (
            handoff["parent_uid"] not in {None, initial["parent_uid"]}
        ),
        "handoff_child_owned_by_replacement_parent": current_owner,
        "handoff_status_rejects_stale_ownership": not stale_status_accepted,
        "final_child_owned_by_replacement_parent": (
            final["statefulset_owner_uid"] == final["parent_uid"]
            and final["pod_owner_uid"] == final["statefulset_uid"]
        ),
    }
    evidence.update(
        {
            "status": "completed",
            "semantic_status": "passed" if all(checks.values()) else "failed",
            "checks": checks,
            "initial": initial,
            "handoff": handoff,
            "final": final,
        }
    )
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("workload")
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    if args.timeout <= 0 or args.poll_seconds <= 0 or args.settle_seconds < 0:
        parser.error(
            "timeouts must be positive and settle-seconds must be non-negative"
        )
    return args


def main() -> int:
    args = parse_args()
    evidence_path = args.output.with_name("ownership-handoff.json")
    try:
        run(args)
    except Exception as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        failed: dict[str, Any] = {
            "status": "harness-failed",
            "semantic_status": "not-evaluated",
            "error": str(exc),
            "workload": args.workload,
        }
        if evidence_path.exists():
            try:
                failed = {**json.loads(evidence_path.read_text()), **failed}
            except json.JSONDecodeError:
                pass
        evidence_path.write_text(json.dumps(failed, indent=2, sort_keys=True) + "\n")
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
