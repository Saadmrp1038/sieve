#!/usr/bin/env python3
"""Exercise ClusterObservability ownership across same-name recreation."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sieve_tool.process import Result, execution
from sieve_tool.process import run as run_process

NAMESPACE = "default"
PARENT = "sieve-cluster-observability"
AGENT = f"{PARENT}-agent"
CLUSTER = f"{PARENT}-cluster"
AGENT_WORKLOAD = f"{AGENT}-collector"
CLUSTER_WORKLOAD = f"{CLUSTER}-collector"
TARGET_ALLOCATOR = f"{PARENT}-targetallocator"
TARGET_ALLOCATOR_IMAGE = (
    "ghcr.io/open-telemetry/opentelemetry-operator/target-allocator:0.162.0"
)
POLICY = "sieve-otel-collector-delete"


class WorkloadError(RuntimeError):
    """The workload could not establish the required evidence."""


class Recorder:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.observations = output.with_name("ownership-observations.jsonl")

    def kubectl(
        self,
        *arguments: str,
        payload: dict[str, Any] | None = None,
        check: bool = True,
        namespaced: bool = True,
    ) -> Result:
        command = ["kubectl"]
        if namespaced:
            command.extend(["--namespace", NAMESPACE])
        command.extend(arguments)
        result = run_process(
            command,
            operation="OpenTelemetry ownership kubectl",
            input=None if payload is None else json.dumps(payload),
            cwd=self.output.parent,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if check and result.returncode:
            raise WorkloadError(
                f"kubectl {' '.join(arguments)} failed ({result.returncode}): "
                f"{result.stderr.strip()}"
            )
        return result

    def object(
        self, kind: str, name: str, *, namespaced: bool = True
    ) -> dict[str, Any] | None:
        result = self.kubectl(
            "get",
            kind,
            name,
            "--ignore-not-found",
            "-o",
            "json",
            check=False,
            namespaced=namespaced,
        )
        if result.returncode or not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    def controller(self) -> dict[str, Any] | None:
        result = self.kubectl(
            "get", "pods", "-l", "sievetag=opentelemetry-operator", "-o", "json"
        )
        pods = json.loads(result.stdout).get("items") or []
        if not pods:
            return None
        pod = sorted(pods, key=lambda item: item["metadata"]["name"])[0]
        status = next(
            (
                item
                for item in pod.get("status", {}).get("containerStatuses") or []
                if item.get("name") == "manager"
            ),
            {},
        )
        return {
            "name": pod["metadata"].get("name"),
            "uid": pod["metadata"].get("uid"),
            "restartCount": status.get("restartCount", 0),
        }

    def observe(self, phase: str) -> dict[str, Any]:
        sample = {
            "phase": phase,
            "monotonicNS": time.monotonic_ns(),
            "controller": self.controller(),
            "parent": self.object("clusterobservability", PARENT),
            "agentCollector": self.object("opentelemetrycollector", AGENT),
            "clusterCollector": self.object("opentelemetrycollector", CLUSTER),
            "instrumentation": self.object("instrumentation", PARENT),
            "agentWorkload": self.object("daemonset", AGENT_WORKLOAD),
            "clusterWorkload": self.object("statefulset", CLUSTER_WORKLOAD),
        }
        with self.observations.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(sample, sort_keys=True) + "\n")
        return sample


def uid(value: dict[str, Any] | None) -> str | None:
    return None if value is None else value.get("metadata", {}).get("uid")


def controller_owner_uid(
    value: dict[str, Any] | None, *, kind: str, name: str
) -> str | None:
    if value is None:
        return None
    for reference in value.get("metadata", {}).get("ownerReferences") or []:
        if (
            reference.get("kind") == kind
            and reference.get("name") == name
            and reference.get("controller") is True
        ):
            return reference.get("uid")
    return None


def current_condition(value: dict[str, Any] | None, condition_type: str) -> bool:
    if value is None:
        return False
    generation = value.get("metadata", {}).get("generation")
    status = value.get("status", {})
    if status.get("observedGeneration") != generation:
        return False
    return any(
        condition.get("type") == condition_type
        and condition.get("status") == "True"
        and condition.get("observedGeneration", generation) == generation
        for condition in status.get("conditions") or []
    )


def parent_ready(sample: dict[str, Any]) -> bool:
    parent = sample.get("parent")
    return bool(
        parent
        and parent.get("status", {}).get("phase") == "Ready"
        and current_condition(parent, "Ready")
    )


def daemonset_ready(value: dict[str, Any] | None) -> bool:
    if value is None or value.get("metadata", {}).get("deletionTimestamp"):
        return False
    status = value.get("status", {})
    desired = status.get("desiredNumberScheduled", 0)
    return desired > 0 and status.get("numberReady") == desired


def statefulset_ready(value: dict[str, Any] | None) -> bool:
    if value is None or value.get("metadata", {}).get("deletionTimestamp"):
        return False
    status = value.get("status", {})
    replicas = status.get("replicas", 0)
    return replicas > 0 and status.get("readyReplicas") == replicas


def owned_by_parent(value: dict[str, Any] | None, parent_uid: str | None) -> bool:
    return (
        parent_uid is not None
        and controller_owner_uid(value, kind="ClusterObservability", name=PARENT)
        == parent_uid
    )


def workloads_owned_by_collectors(sample: dict[str, Any]) -> bool:
    return controller_owner_uid(
        sample.get("agentWorkload"), kind="OpenTelemetryCollector", name=AGENT
    ) == uid(sample.get("agentCollector")) and controller_owner_uid(
        sample.get("clusterWorkload"),
        kind="OpenTelemetryCollector",
        name=CLUSTER,
    ) == uid(sample.get("clusterCollector"))


def terminal_sample(sample: dict[str, Any]) -> bool:
    return bool(
        parent_ready(sample)
        and sample.get("agentCollector")
        and sample.get("clusterCollector")
        and sample.get("instrumentation")
        and daemonset_ready(sample.get("agentWorkload"))
        and statefulset_ready(sample.get("clusterWorkload"))
        and workloads_owned_by_collectors(sample)
    )


def all_children_owned(sample: dict[str, Any]) -> bool:
    parent_uid = uid(sample.get("parent"))
    return all(
        owned_by_parent(sample.get(key), parent_uid)
        for key in ("agentCollector", "clusterCollector", "instrumentation")
    )


def wait_for(
    recorder: Recorder,
    phase: str,
    timeout: float,
    poll_seconds: float,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    settle_seconds: float = 0,
    stability_key: Callable[[dict[str, Any]], object] | None = None,
) -> dict[str, Any]:
    end = time.monotonic() + timeout
    stable_since: float | None = None
    stable_value: object | None = None
    last: dict[str, Any] | None = None
    while time.monotonic() < end:
        last = recorder.observe(phase)
        now = time.monotonic()
        value = stability_key(last) if stability_key else None
        if predicate(last):
            if stable_since is None or value != stable_value:
                stable_since = now
                stable_value = value
            if now - stable_since >= settle_seconds:
                return last
        else:
            stable_since = None
            stable_value = None
        time.sleep(min(poll_seconds, max(0, end - time.monotonic())))
    raise WorkloadError(
        f"timed out waiting for {phase}; last sample: "
        + json.dumps(last, sort_keys=True)
    )


def apply(recorder: Recorder, manifest: dict[str, Any]) -> dict[str, Any]:
    result = recorder.kubectl("apply", "-f", "-", "-o", "json", payload=manifest)
    return json.loads(result.stdout)


def parent_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "opentelemetry.io/v1alpha1",
        "kind": "ClusterObservability",
        "metadata": {"name": PARENT},
        "spec": {
            "exporter": {
                "endpoint": "http://sieve-otel-sink.default.svc.cluster.local:4318"
            }
        },
    }


def target_allocator_manifest() -> dict[str, Any]:
    return {
        "apiVersion": "opentelemetry.io/v1alpha1",
        "kind": "TargetAllocator",
        "metadata": {"name": TARGET_ALLOCATOR},
        "spec": {
            "image": TARGET_ALLOCATOR_IMAGE,
            "scrapeConfigs": [
                {"job_name": "sieve-noop", "static_configs": [{"targets": []}]}
            ],
        },
    }


def policy_manifests(action: str = "Deny") -> tuple[dict[str, Any], dict[str, Any]]:
    policy = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicy",
        "metadata": {"name": POLICY},
        "spec": {
            "failurePolicy": "Fail",
            "matchConstraints": {
                "resourceRules": [
                    {
                        "apiGroups": ["opentelemetry.io"],
                        "apiVersions": ["v1beta1"],
                        "operations": ["DELETE"],
                        "resources": ["opentelemetrycollectors"],
                        "scope": "Namespaced",
                    }
                ]
            },
            "validations": [
                {
                    "expression": (
                        "oldObject.metadata.namespace != 'default' || "
                        f"!(oldObject.metadata.name in ['{AGENT}', '{CLUSTER}'])"
                    ),
                    "message": "sieve synthetic OpenTelemetryCollector delete denial",
                }
            ],
        },
    }
    binding = {
        "apiVersion": "admissionregistration.k8s.io/v1",
        "kind": "ValidatingAdmissionPolicyBinding",
        "metadata": {"name": POLICY},
        "spec": {"policyName": POLICY, "validationActions": [action]},
    }
    return policy, binding


def validate_controller(recorder: Recorder) -> dict[str, Any]:
    deployment = recorder.object("deployment", "opentelemetry-operator")
    if deployment is None:
        raise WorkloadError("opentelemetry-operator Deployment is missing")
    if deployment.get("spec", {}).get("replicas") != 1:
        raise WorkloadError("experiment requires one native operator replica")
    containers = deployment["spec"]["template"]["spec"]["containers"]
    manager = next(item for item in containers if item["name"] == "manager")
    arguments = manager.get("args") or []
    if not any(
        item.startswith("--feature-gates=") and "operator.clusterobservability" in item
        for item in arguments
    ):
        raise WorkloadError("ClusterObservability feature gate is not enabled")
    return deployment


def deletion_denial_probe(recorder: Recorder) -> dict[str, Any]:
    result = recorder.kubectl(
        "delete",
        "opentelemetrycollector",
        AGENT,
        "--dry-run=server",
        "--wait=false",
        check=False,
    )
    denied = result.returncode != 0 and (
        "sieve synthetic OpenTelemetryCollector delete denial" in result.stderr
        or "denied" in result.stderr.lower()
    )
    if not denied:
        raise WorkloadError(
            "synthetic collector deletion was not denied: " + result.stderr.strip()
        )
    return {
        "denied": True,
        "returncode": result.returncode,
        "stderr": result.stderr,
    }


def remove_policy(recorder: Recorder) -> None:
    recorder.kubectl(
        "delete",
        "validatingadmissionpolicybinding",
        POLICY,
        "--ignore-not-found",
        namespaced=False,
        check=False,
    )
    recorder.kubectl(
        "delete",
        "validatingadmissionpolicy",
        POLICY,
        "--ignore-not-found",
        namespaced=False,
        check=False,
    )


def record_outcome(
    output: Path, evidence: dict[str, Any], *, error: str | None = None
) -> None:
    if error is not None:
        evidence["error"] = error
        evidence["traceback"] = traceback.format_exc()
    output.with_name("opentelemetry-cluster-ownership.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    )
    if checks := evidence.get("checks"):
        output.with_name("semantic-outcome.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "checks": [
                        {"name": name, "passed": passed}
                        for name, passed in checks.items()
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    if error is None:
        output.write_text("FINISH-SIEVE-TEST\n")


def run(args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    recorder = Recorder(args.output)
    evidence["operatorDeployment"] = validate_controller(recorder)
    block_deletion = os.environ.get("SIEVE_OTEL_BLOCK_COLLECTOR_DELETE") == "1"
    evidence["faultMode"] = "deny-collector-delete" if block_deletion else "none"

    evidence["targetAllocatorPrerequisite"] = apply(
        recorder, target_allocator_manifest()
    )
    apply(recorder, parent_manifest())
    initial = wait_for(
        recorder,
        "initial",
        args.timeout,
        args.poll_seconds,
        lambda sample: terminal_sample(sample) and all_children_owned(sample),
        settle_seconds=args.settle_seconds,
        stability_key=lambda sample: (
            uid(sample.get("parent")),
            uid(sample.get("agentCollector")),
            uid(sample.get("clusterCollector")),
            (sample.get("controller") or {}).get("uid"),
        ),
    )
    evidence["initial"] = initial
    old_parent_uid = uid(initial.get("parent"))
    evidence["oldParentUID"] = old_parent_uid
    old_agent_uid = uid(initial.get("agentCollector"))
    old_cluster_uid = uid(initial.get("clusterCollector"))

    if block_deletion:
        policy, binding = policy_manifests()
        apply(recorder, policy)
        apply(recorder, binding)
        evidence["deletionProbe"] = deletion_denial_probe(recorder)

    recorder.kubectl(
        "delete", "clusterobservability", PARENT, "--wait=false", "--timeout", "60s"
    )
    if block_deletion:
        wait_for(
            recorder,
            "old-parent-deleted-retained-collectors",
            args.timeout,
            args.poll_seconds,
            lambda sample: (
                sample.get("parent") is None
                and sample.get("instrumentation") is None
                and uid(sample.get("agentCollector")) == old_agent_uid
                and uid(sample.get("clusterCollector")) == old_cluster_uid
                and owned_by_parent(sample.get("agentCollector"), old_parent_uid)
                and owned_by_parent(sample.get("clusterCollector"), old_parent_uid)
            ),
        )
    else:
        wait_for(
            recorder,
            "old-parent-and-children-deleted",
            args.timeout,
            args.poll_seconds,
            lambda sample: all(
                sample.get(key) is None
                for key in (
                    "parent",
                    "agentCollector",
                    "clusterCollector",
                    "instrumentation",
                    "agentWorkload",
                    "clusterWorkload",
                )
            ),
        )

    apply(recorder, parent_manifest())
    handoff = wait_for(
        recorder,
        "replacement-terminal-state",
        args.timeout,
        args.poll_seconds,
        lambda sample: (
            uid(sample.get("parent")) not in {None, old_parent_uid}
            and terminal_sample(sample)
        ),
        settle_seconds=args.settle_seconds,
        stability_key=lambda sample: (
            uid(sample.get("parent")),
            controller_owner_uid(
                sample.get("agentCollector"),
                kind="ClusterObservability",
                name=PARENT,
            ),
            controller_owner_uid(
                sample.get("clusterCollector"),
                kind="ClusterObservability",
                name=PARENT,
            ),
            controller_owner_uid(
                sample.get("instrumentation"),
                kind="ClusterObservability",
                name=PARENT,
            ),
            (sample.get("controller") or {}).get("uid"),
        ),
    )
    evidence["handoff"] = handoff
    new_parent_uid = uid(handoff.get("parent"))
    evidence["newParentUID"] = new_parent_uid

    agent_owned = owned_by_parent(handoff.get("agentCollector"), new_parent_uid)
    cluster_owned = owned_by_parent(handoff.get("clusterCollector"), new_parent_uid)
    instrumentation_owned = owned_by_parent(
        handoff.get("instrumentation"), new_parent_uid
    )
    checks = {
        "replacement_parent_has_new_uid": new_parent_uid not in {None, old_parent_uid},
        "instrumentation_owned_by_replacement": instrumentation_owned,
        "agent_collector_owned_by_replacement": agent_owned,
        "cluster_collector_owned_by_replacement": cluster_owned,
        "ready_requires_current_child_ownership": not (
            parent_ready(handoff)
            and not (agent_owned and cluster_owned and instrumentation_owned)
        ),
    }
    evidence["checks"] = checks
    evidence["semantic_status"] = "passed" if all(checks.values()) else "failed"

    if block_deletion:
        _, audit_binding = policy_manifests("Audit")
        apply(recorder, audit_binding)
        recovery = wait_for(
            recorder,
            "current-ownership-recovery",
            args.timeout,
            args.poll_seconds,
            lambda sample: (
                uid(sample.get("parent")) == new_parent_uid
                and terminal_sample(sample)
                and all_children_owned(sample)
            ),
            settle_seconds=args.settle_seconds,
            stability_key=lambda sample: (
                uid(sample.get("agentCollector")),
                uid(sample.get("clusterCollector")),
                (sample.get("controller") or {}).get("uid"),
            ),
        )
        evidence["recovery"] = {
            "all_children_owned_by_replacement": all_children_owned(recovery),
            "sample": recovery,
        }
        remove_policy(recorder)
    else:
        evidence["deletionProbe"] = None
        evidence["recovery"] = {
            "all_children_owned_by_replacement": all_children_owned(handoff),
            "sample": handoff,
        }

    evidence["status"] = "completed"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["cluster-ownership-handoff"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()
    limits = (args.timeout, args.poll_seconds, args.settle_seconds)
    if not all(math.isfinite(value) for value in limits):
        parser.error("timeouts must be finite")
    if args.timeout <= 0 or args.poll_seconds <= 0 or args.settle_seconds < 0:
        parser.error(
            "timeouts must be positive and settle-seconds must be non-negative"
        )
    return args


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "workload": args.workload,
        "status": "configuration-failed",
        "semantic_status": "not-evaluated",
    }
    try:
        with execution(
            {"request": 60, "workload": 1200},
            args.output.parent / "kubectl-commands",
        ):
            run(args, evidence)
    except Exception as error:
        record_outcome(args.output, evidence, error=str(error))
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if os.environ.get("SIEVE_OTEL_BLOCK_COLLECTOR_DELETE") == "1":
            try:
                recorder = Recorder(args.output)
                _, audit_binding = policy_manifests("Audit")
                apply(recorder, audit_binding)
                remove_policy(recorder)
            except Exception:
                pass

    record_outcome(args.output, evidence)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
