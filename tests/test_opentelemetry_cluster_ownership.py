"""OpenTelemetry ClusterObservability ownership checks use the public workload CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("block_deletion", [False, True])
def test_cluster_ownership_handoff_records_control_and_fault_outcomes(
    tmp_path, block_deletion
):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["OTEL_OWNERSHIP_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "parent": "absent",
    "target_allocator": None,
    "policy": False,
    "binding": False,
    "audit": False,
    "probe_attempts": 0,
}
args = sys.argv[1:]
if args[:2] == ["--namespace", "default"]:
    args = args[2:]
block_deletion = os.environ.get("SIEVE_OTEL_BLOCK_COLLECTOR_DELETE") == "1"


def save():
    state_path.write_text(json.dumps(state))


def emit(value):
    save()
    print(json.dumps(value))
    raise SystemExit(0)


def clean(arguments):
    result = []
    skip = False
    for item in arguments:
        if skip:
            skip = False
            continue
        if item in {"-o", "--output", "--timeout"}:
            skip = True
            continue
        if item in {"--ignore-not-found", "--wait=true", "--wait=false"}:
            continue
        result.append(item)
    return result


def owner(uid):
    return [{
        "apiVersion": "opentelemetry.io/v1alpha1",
        "kind": "ClusterObservability",
        "name": "sieve-cluster-observability",
        "uid": uid,
        "controller": True,
    }]


def parent():
    uid = "parent-old" if state["parent"] == "old" else "parent-new"
    return {
        "apiVersion": "opentelemetry.io/v1alpha1",
        "kind": "ClusterObservability",
        "metadata": {"name": "sieve-cluster-observability", "uid": uid, "generation": 1},
        "status": {
            "observedGeneration": 1,
            "phase": "Ready",
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}],
        },
    }


def collector(name):
    recovered = state["audit"] or (
        not block_deletion and state["parent"] == "new"
    )
    parent_uid = "parent-new" if recovered else "parent-old"
    uid = name + ("-new" if recovered else "-old")
    return {
        "apiVersion": "opentelemetry.io/v1beta1",
        "kind": "OpenTelemetryCollector",
        "metadata": {"name": name, "uid": uid, "ownerReferences": owner(parent_uid)},
        "status": {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]},
    }


def instrumentation():
    return {
        "apiVersion": "opentelemetry.io/v1alpha1",
        "kind": "Instrumentation",
        "metadata": {
            "name": "sieve-cluster-observability",
            "uid": "instrumentation-new" if state["parent"] == "new" else "instrumentation-old",
            "ownerReferences": owner("parent-new" if state["parent"] == "new" else "parent-old"),
        },
    }


def workload(kind, name):
    child = name.removesuffix("-collector")
    recovered = state["audit"] or (
        not block_deletion and state["parent"] == "new"
    )
    child_uid = child + ("-new" if recovered else "-old")
    status = {"readyReplicas": 1, "replicas": 1}
    if kind == "daemonset":
        status = {"desiredNumberScheduled": 1, "numberReady": 1}
    return {
        "apiVersion": "apps/v1",
        "kind": "DaemonSet" if kind == "daemonset" else "StatefulSet",
        "metadata": {
            "name": name,
            "uid": name + "-uid",
            "ownerReferences": [{
                "apiVersion": "opentelemetry.io/v1beta1",
                "kind": "OpenTelemetryCollector",
                "name": child,
                "uid": child_uid,
                "controller": True,
            }],
        },
        "status": status,
    }


a = clean(args)
if a[:3] == ["get", "deployment", "opentelemetry-operator"]:
    emit({
        "metadata": {"name": "opentelemetry-operator"},
        "spec": {"replicas": 1, "template": {"spec": {"containers": [{
            "name": "manager",
            "args": ["--enable-webhooks=false", "--feature-gates=operator.clusterobservability"],
        }]}}},
    })
if a[:2] == ["get", "pods"]:
    emit({"items": [{
        "metadata": {"name": "opentelemetry-operator-1", "uid": "controller-uid"},
        "status": {"containerStatuses": [{"name": "manager", "restartCount": 0}]},
    }]})
if a[:2] == ["apply", "-f"]:
    value = json.load(sys.stdin)
    kind = value["kind"]
    if kind == "TargetAllocator":
        state["target_allocator"] = value
        emit(value)
    if kind == "ClusterObservability":
        state["parent"] = "old" if state["parent"] == "absent" else "new"
        emit(parent())
    if kind == "ValidatingAdmissionPolicy":
        state["policy"] = True
        emit(value)
    if kind == "ValidatingAdmissionPolicyBinding":
        state["binding"] = True
        state["audit"] = value["spec"]["validationActions"] == ["Audit"]
        emit(value)
if a[:3] == ["delete", "clusterobservability", "sieve-cluster-observability"]:
    state["parent"] = "deleted"
    emit({"status": "Success"})
if a[:3] == ["delete", "opentelemetrycollector", "sieve-cluster-observability-agent"]:
    if state["audit"]:
        emit({"status": "Success"})
    state["probe_attempts"] += 1
    if state["probe_attempts"] < 3:
        save()
        print("opentelemetrycollector.opentelemetry.io/sieve-cluster-observability-agent deleted (server dry run)")
        raise SystemExit(0)
    save()
    print("sieve synthetic OpenTelemetryCollector delete denial", file=sys.stderr)
    raise SystemExit(1)
if a[:3] == ["get", "clusterobservability", "sieve-cluster-observability"]:
    if state["parent"] == "deleted":
        save()
        raise SystemExit(0)
    emit(parent())
if a[:2] == ["get", "opentelemetrycollector"]:
    if state["parent"] == "deleted" and not block_deletion:
        save()
        raise SystemExit(0)
    emit(collector(a[2]))
if a[:3] == ["get", "instrumentation", "sieve-cluster-observability"]:
    if state["parent"] == "deleted":
        save()
        raise SystemExit(0)
    emit(instrumentation())
if a[:2] == ["get", "daemonset"]:
    if state["parent"] == "deleted" and not block_deletion:
        save()
        raise SystemExit(0)
    emit(workload("daemonset", a[2]))
if a[:2] == ["get", "statefulset"]:
    if state["parent"] == "deleted" and not block_deletion:
        save()
        raise SystemExit(0)
    emit(workload("statefulset", a[2]))
if a[:2] == ["get", "validatingadmissionpolicy"]:
    emit({"metadata": {"name": a[2]}, "spec": {"failurePolicy": "Fail"}})
if a[:2] == ["get", "validatingadmissionpolicybinding"]:
    emit({"metadata": {"name": a[2]}, "spec": {"validationActions": ["Deny"]}})
if a[:2] == ["delete", "validatingadmissionpolicybinding"] or a[:2] == ["delete", "validatingadmissionpolicy"]:
    emit({"status": "Success"})
save()
raise SystemExit("unexpected kubectl command: " + repr(args))
"""
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"

    environment = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "OTEL_OWNERSHIP_STATE": str(tmp_path / "state.json"),
    }
    if block_deletion:
        environment["SIEVE_OTEL_BLOCK_COLLECTOR_DELETE"] = "1"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/opentelemetry-operator/workloads/workload.py"),
            "cluster-ownership-handoff",
            str(output),
            "--timeout",
            "2.5",
            "--poll-seconds",
            "0.01",
            "--settle-seconds",
            "0.02",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(
        output.with_name("opentelemetry-cluster-ownership.json").read_text()
    )
    assert evidence["status"] == "completed"
    assert evidence["targetAllocatorPrerequisite"]["metadata"]["name"] == (
        "sieve-cluster-observability-targetallocator"
    )
    assert evidence["targetAllocatorPrerequisite"]["spec"]["image"] == (
        "ghcr.io/open-telemetry/opentelemetry-operator/target-allocator:0.162.0"
    )
    assert evidence["oldParentUID"] == "parent-old"
    assert evidence["newParentUID"] == "parent-new"
    assert evidence["checks"]["replacement_parent_has_new_uid"] is True
    assert evidence["checks"]["instrumentation_owned_by_replacement"] is True
    assert evidence["recovery"]["all_children_owned_by_replacement"] is True
    if block_deletion:
        assert evidence["semantic_status"] == "failed"
        assert evidence["deletionProbe"]["denied"] is True
        assert evidence["deletionProbe"]["attempts"] == 3
        assert evidence["auditProbe"]["denied"] is False
        assert evidence["checks"]["agent_collector_owned_by_replacement"] is False
        assert evidence["checks"]["cluster_collector_owned_by_replacement"] is False
        assert evidence["checks"]["ready_requires_current_child_ownership"] is False
    else:
        assert evidence["semantic_status"] == "passed"
        assert evidence["deletionProbe"] is None
        assert evidence["auditProbe"] is None
        assert evidence["checks"]["agent_collector_owned_by_replacement"] is True
        assert evidence["checks"]["cluster_collector_owned_by_replacement"] is True
        assert evidence["checks"]["ready_requires_current_child_ownership"] is True
    outcome = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert {item["name"]: item["passed"] for item in outcome["checks"]} == evidence[
        "checks"
    ]


def test_cluster_ownership_handoff_reports_kubectl_read_failures(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "print('synthetic API outage', file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/opentelemetry-operator/workloads/workload.py"),
            "cluster-ownership-handoff",
            str(output),
            "--timeout",
            "0.1",
            "--poll-seconds",
            "0.01",
            "--settle-seconds",
            "0",
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 1
    evidence = json.loads(
        output.with_name("opentelemetry-cluster-ownership.json").read_text()
    )
    assert "synthetic API outage" in evidence["error"]
    assert "Deployment is missing" not in evidence["error"]


def test_cluster_ownership_handoff_records_cleanup_failures(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import sys

args = sys.argv[1:]
if args[:2] == ["--namespace", "default"]:
    args = args[2:]
if args[:2] == ["get", "deployment"]:
    print("primary API outage", file=sys.stderr)
    raise SystemExit(1)
if args[:2] == ["apply", "-f"]:
    print("cleanup API outage", file=sys.stderr)
    raise SystemExit(1)
if args and args[0] == "delete":
    print("cleanup API outage", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit("unexpected kubectl command: " + repr(args))
"""
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/opentelemetry-operator/workloads/workload.py"),
            "cluster-ownership-handoff",
            str(output),
            "--timeout",
            "0.1",
            "--poll-seconds",
            "0.01",
            "--settle-seconds",
            "0",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "SIEVE_OTEL_BLOCK_COLLECTOR_DELETE": "1",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 1
    evidence = json.loads(
        output.with_name("opentelemetry-cluster-ownership.json").read_text()
    )
    assert "primary API outage" in evidence["error"]
    assert any("cleanup API outage" in message for message in evidence["cleanupErrors"])
    assert "cleanup API outage" in result.stderr
