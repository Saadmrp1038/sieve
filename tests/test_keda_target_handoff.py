"""KEDA target restoration is adjudicated through the public workload CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sieve_tool.common.config import load_controller_config

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("mode", "workload", "expected_target", "expected_original", "passed"),
    [
        ("control-a", "restore-control-a", "sieve-scale-a", 4, True),
        ("control-b", "restore-control-b", "sieve-scale-b", 1, True),
        ("handoff", "target-handoff", "sieve-scale-b", 1, False),
    ],
)
def test_target_handoff_compares_current_target_with_its_own_original_count(
    tmp_path, mode, workload, expected_target, expected_original, passed
):
    profile = ROOT / "controllers/keda"
    config = load_controller_config(profile)
    assert config.commit == "626ded5d783108bedf647e20bbc83f8e458e02e4"
    assert config.client_go_version == "v0.36.4"
    assert config.annotated_reconcile_functions == {
        "controllers/keda/scaledobject_controller.go": (
            "github.com/kedacore/keda/v2/controllers/keda."
            "(*ScaledObjectReconciler).Reconcile"
        )
    }
    manifest = yaml.safe_load((profile / "deploy/operator.yaml").read_text())
    container = manifest["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "keda-operator"
    assert container["image"] == "${SIEVE-DR}/keda:${SIEVE-DT}"
    assert {item["name"]: item.get("value") for item in container["env"]}[
        "KEDA_SCALEDOBJECT_CTRL_MAX_RECONCILES"
    ] == "10"
    build = (profile / "build/build.sh").read_text()
    assert "GOTOOLCHAIN=go1.26.0" in build
    assert "SIEVE_SUBJECT_COMMIT" in build
    assert "./cmd/operator" in build
    deploy = (profile / "deploy/deploy.sh").read_text()
    assert "rollout status deployment/keda-admission --timeout=300s" in deploy
    assert "rollout status deployment/keda-admission-webhooks" not in deploy

    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["KEDA_HANDOFF_STATE"])
state = json.loads(path.read_text()) if path.exists() else {
    "calls": [], "deployments": {}, "scaledobject": None, "hpa": None,
}
args = sys.argv[1:]
state["calls"].append(args)

def save():
    path.write_text(json.dumps(state))

def done(value=None):
    save()
    if value is not None:
        print(json.dumps(value))
    raise SystemExit(0)

def manifest():
    return json.load(sys.stdin)

def deployment(name, replicas):
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {
            "name": name, "namespace": "default", "uid": "uid-" + name,
            "generation": 1,
        },
        "spec": {"replicas": replicas},
        "status": {
            "observedGeneration": 1, "replicas": replicas,
            "readyReplicas": replicas, "availableReplicas": replicas,
        },
    }

def set_replicas(name, replicas):
    target = state["deployments"][name]
    target["spec"]["replicas"] = replicas
    target["status"].update(
        replicas=replicas, readyReplicas=replicas, availableReplicas=replicas
    )

def scaledobject(name):
    original = state["deployments"][name]["spec"]["replicas"]
    return {
        "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject",
        "metadata": {
            "name": "sieve-target-handoff", "namespace": "default",
            "uid": "uid-scaledobject", "generation": 1,
            "finalizers": ["finalizer.keda.sh"],
        },
        "spec": {
            "scaleTargetRef": {"name": name},
            "minReplicaCount": 2, "maxReplicaCount": 2,
            "advanced": {"restoreToOriginalReplicaCount": True},
        },
        "status": {
            "originalReplicaCount": original,
            "scaleTargetGVKR": {
                "group": "apps", "version": "v1", "kind": "Deployment",
                "resource": "deployments",
            },
            "conditions": [{
                "type": "Ready", "status": "True", "observedGeneration": 1,
            }],
        },
    }

def hpa(name):
    return {
        "apiVersion": "autoscaling/v2", "kind": "HorizontalPodAutoscaler",
        "metadata": {
            "name": "keda-hpa-sieve-target-handoff",
            "namespace": "default", "uid": "uid-hpa", "generation": 1,
        },
        "spec": {
            "minReplicas": 2, "maxReplicas": 2,
            "scaleTargetRef": {
                "apiVersion": "apps/v1", "kind": "Deployment", "name": name,
            },
        },
        "status": {"currentReplicas": 2, "desiredReplicas": 2},
    }

def clean(arguments):
    result = []
    skip = False
    for i, item in enumerate(arguments):
        if skip:
            skip = False
            continue
        if item in {"-n", "--namespace"}:
            skip = True
            continue
        if item in {"-o", "--output"}:
            skip = True
            continue
        if item == "--ignore-not-found":
            continue
        result.append(item)
    return result

a = clean(args)
if a[:3] == ["get", "deployment", "keda-operator"]:
    done({
        "metadata": {"name": "keda-operator", "namespace": "default"},
        "spec": {"template": {"spec": {"containers": [{
            "name": "keda-operator",
            "image": "registry.local/keda:vanilla",
            "env": [{
                "name": "KEDA_SCALEDOBJECT_CTRL_MAX_RECONCILES", "value": "10",
            }],
        }]}}},
    })
if a[:3] == ["get", "customresourcedefinition", "scaledobjects.keda.sh"]:
    done({"metadata": {"name": "scaledobjects.keda.sh"}})
if a[:2] == ["get", "pods"]:
    done({"items": [{
        "metadata": {
            "name": "keda-operator-1", "namespace": "default",
            "uid": "uid-keda-operator-1", "labels": {"sievetag": "keda"},
        },
        "spec": {"containers": [{"name": "keda-operator"}]},
        "status": {"containerStatuses": [{
            "name": "keda-operator", "ready": True, "restartCount": 0,
            "imageID": "registry.local/keda@sha256:fixture",
        }]},
    }]})
if a[:2] == ["apply", "-f"]:
    value = manifest()
    kind = value["kind"].lower()
    name = value["metadata"]["name"]
    if kind == "deployment":
        created = deployment(name, value["spec"]["replicas"])
        state["deployments"][name] = created
        done(created)
    if kind == "scaledobject":
        target = value["spec"]["scaleTargetRef"]["name"]
        state["scaledobject"] = scaledobject(target)
        state["hpa"] = hpa(target)
        set_replicas(target, 2)
        done(state["scaledobject"])
if a[:3] == ["patch", "scaledobject", "sieve-target-handoff"]:
    patch = json.loads(a[a.index("-p") + 1])
    target = patch["spec"]["scaleTargetRef"]["name"]
    state["scaledobject"]["spec"]["scaleTargetRef"]["name"] = target
    state["scaledobject"]["metadata"]["generation"] += 1
    state["scaledobject"]["status"]["conditions"][0][
        "observedGeneration"
    ] = state["scaledobject"]["metadata"]["generation"]
    state["hpa"] = hpa(target)
    set_replicas(target, 2)
    done(state["scaledobject"])
if a[:3] == ["delete", "scaledobject", "sieve-target-handoff"]:
    target = state["scaledobject"]["spec"]["scaleTargetRef"]["name"]
    original = state["scaledobject"]["status"]["originalReplicaCount"]
    set_replicas(target, original)
    state["scaledobject"] = None
    state["hpa"] = None
    done()
if a[:2] == ["delete", "deployment"]:
    for name in a[2:]:
        if not name.startswith("--"):
            state["deployments"].pop(name, None)
    done()
if a[:2] == ["get", "deployment"]:
    done(state["deployments"].get(a[2]))
if a[:3] == ["get", "scaledobject", "sieve-target-handoff"]:
    done(state["scaledobject"])
if a[:3] == ["get", "horizontalpodautoscaler", "keda-hpa-sieve-target-handoff"]:
    done(state["hpa"])
raise SystemExit("unexpected kubectl command: " + repr(args))
"""
    )
    kubectl.chmod(0o700)
    output = tmp_path / "result/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/keda/workloads/workload.py"),
            workload,
            str(output),
            "--timeout",
            "2",
            "--poll-seconds",
            "0.01",
            "--settle-seconds",
            "0.02",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "KEDA_HANDOFF_STATE": str(tmp_path / "cluster.json"),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("target-handoff.json").read_text())
    assert evidence["status"] == "completed"
    assert evidence["semantic_status"] == ("passed" if passed else "failed")
    assert evidence["current_target"] == expected_target
    assert evidence["expected_original_replica_count"] == expected_original
    assert evidence["checks"]["controller_uses_ten_workers"] is True
    assert evidence["checks"]["hpa_follows_current_target"] is True
    assert evidence["checks"]["current_target_restored_to_its_original_count"] is passed
    assert evidence["final"]["current_target_replicas"] == (
        expected_original if passed else 4
    )
    if mode == "handoff":
        assert evidence["after_handoff"]["saved_original_replica_count"] == 4
        assert (
            evidence["accepted_target_update"]["spec"]["scaleTargetRef"]["name"]
            == "sieve-scale-b"
        )
