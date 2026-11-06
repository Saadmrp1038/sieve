"""Prometheus ownership handoff is adjudicated through the public workload CLI."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sieve_tool.common.config import load_controller_config

ROOT = Path(__file__).resolve().parents[1]


def test_profile_pins_native_release_graph_and_reconcile_loop():
    profile = ROOT / "controllers/prometheus-operator"
    config = load_controller_config(profile)
    assert config.commit == "0db90444c4e6fa5672be5bf7d2b15ca9dd1c5ace"
    assert config.client_go_version == "v0.37.0"
    assert config.annotated_reconcile_functions == {
        "pkg/prometheus/server/operator.go": (
            "github.com/prometheus-operator/prometheus-operator/"
            "pkg/prometheus/server.(*Operator).Sync"
        )
    }

    deployment = yaml.safe_load((profile / "deploy/operator.yaml").read_text())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "prometheus-operator"
    assert not any("concurr" in arg for arg in container["args"])
    assert any(
        arg.startswith("--prometheus-config-reloader=") and "@sha256:" in arg
        for arg in container["args"]
    )

    build = (profile / "build/build.sh").read_text()
    assert "GOTOOLCHAIN=go1.26.0" in build
    assert "./cmd/operator" in build
    assert "SIEVE_SUBJECT_COMMIT" in build


def test_deployment_preserves_upstream_crd_yaml_while_removing_operator(tmp_path):
    profile = ROOT / "controllers/prometheus-operator"
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "deploy.sh").write_bytes((profile / "deploy/deploy.sh").read_bytes())
    (deploy / "operator.yaml").write_bytes(
        (profile / "deploy/operator.yaml").read_bytes()
    )

    tools = tmp_path / "tools"
    tools.mkdir()
    bundle = """---
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: fixtures.example.test
spec:
  schema:
    openAPIV3Schema:
      enum:
      - =
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus-operator
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus-operator
"""
    curl = tools / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import sys\nfrom pathlib import Path\n"
        f"Path(sys.argv[sys.argv.index('--output') + 1]).write_text({bundle!r})\n"
    )
    curl.chmod(0o700)
    kubectl = tools / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nfrom pathlib import Path\n"
        "with Path(os.environ['DEPLOY_CALLS']).open('a') as f: "
        "f.write(' '.join(sys.argv[1:]) + '\\n')\n"
    )
    kubectl.chmod(0o700)

    package = tmp_path / "fixture-python/sieve_tool"
    (package / "runtime").mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "runtime/__init__.py").touch()
    (package / "process.py").write_text(
        "from contextlib import contextmanager\n"
        "@contextmanager\n"
        "def execution(**kwargs):\n yield\n"
    )
    (package / "runtime/cluster.py").write_text(
        "def pull_node_image(image):\n return 'fixture-digest'\n"
    )
    calls = tmp_path / "calls.log"
    result = subprocess.run(
        ["sh", "./deploy.sh"],
        cwd=deploy,
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(package.parent),
            "DEPLOY_CALLS": str(calls),
            "SIEVE_PROMETHEUS_BUNDLE_SHA256": hashlib.sha256(
                bundle.encode()
            ).hexdigest(),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    resources = (deploy / "resources.yaml").read_text()
    assert "kind: CustomResourceDefinition" in resources
    assert "      - =" in resources
    assert "kind: ServiceAccount" in resources
    assert "kind: Deployment" not in resources
    assert calls.read_text().splitlines() == [
        "apply --server-side -f resources.yaml",
        "apply -f operator.yaml",
    ]


@pytest.mark.parametrize("mode", ["control", "rejected"])
def test_same_name_parent_recreation_checks_child_uid_ownership(tmp_path, mode):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys
from pathlib import Path

state_path = Path(os.environ["PROMETHEUS_HANDOFF_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "calls": [], "parent": None, "statefulset": None, "pod": None,
    "parent_counter": 0, "statefulset_counter": 0, "pod_counter": 0,
    "policy": False, "pending_replacement": 0,
}
args = sys.argv[1:]
state["calls"].append(args)

def save():
    state_path.write_text(json.dumps(state))

def done(value=None):
    save()
    if value is not None:
        print(json.dumps(value))
    raise SystemExit(0)

def fail(message, code=1):
    save()
    print(message, file=sys.stderr)
    raise SystemExit(code)

def value(flag):
    return args[args.index(flag) + 1]

def create_statefulset():
    state["statefulset_counter"] += 1
    state["pod_counter"] += 1
    owner_uid = state["parent"]["metadata"]["uid"]
    state["statefulset"] = {
        "apiVersion": "apps/v1", "kind": "StatefulSet",
        "metadata": {
            "name": "prometheus-sieve-handoff", "namespace": "default",
            "uid": f"statefulset-{state['statefulset_counter']}",
            "generation": 1,
            "annotations": {"prometheus-operator-input-hash": "fixed-hash"},
            "ownerReferences": [{
                "apiVersion": "monitoring.coreos.com/v1", "kind": "Prometheus",
                "name": "sieve-handoff", "uid": owner_uid,
                "controller": True, "blockOwnerDeletion": True,
            }],
        },
        "spec": {"replicas": 1},
        "status": {"observedGeneration": 1, "readyReplicas": 1, "replicas": 1},
    }
    state["pod"] = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {
            "name": "prometheus-sieve-handoff-0", "namespace": "default",
            "uid": f"pod-{state['pod_counter']}",
            "ownerReferences": [{
                "apiVersion": "apps/v1", "kind": "StatefulSet",
                "name": "prometheus-sieve-handoff",
                "uid": state["statefulset"]["metadata"]["uid"],
                "controller": True,
            }],
        },
        "status": {
            "phase": "Running",
            "containerStatuses": [{"name": "prometheus", "ready": True, "restartCount": 0}],
        },
    }

if args[:2] == ["get", "deployment"]:
    done({
        "metadata": {"name": "prometheus-operator", "namespace": "default"},
        "spec": {"template": {"spec": {"containers": [{
            "name": "prometheus-operator",
            "args": ["--log-level=debug", "--localhost=127.0.0.1"],
        }]}}},
    })

if args[:2] == ["get", "customresourcedefinition"]:
    if args[2] == "prometheuses.monitoring.coreos.com":
        done({"metadata": {"name": args[2]}})
    fail("NotFound", 1)

if args[:2] == ["apply", "-f"]:
    manifest = json.load(sys.stdin)
    kind = manifest["kind"]
    if kind == "Prometheus":
        state["parent_counter"] += 1
        uid = f"prometheus-{state['parent_counter']}"
        state["parent"] = manifest
        state["parent"].setdefault("metadata", {}).update({
            "namespace": "default", "uid": uid, "generation": 1,
        })
        state["parent"]["status"] = {
            "observedGeneration": 1,
            "conditions": [
                {"type": "Reconciled", "status": "True", "observedGeneration": 1},
                {"type": "Available", "status": "True", "observedGeneration": 1},
            ],
            "replicas": 1, "availableReplicas": 1, "updatedReplicas": 1,
        }
        if state["statefulset"] is None:
            create_statefulset()
        done(state["parent"])
    if kind in {"ValidatingAdmissionPolicy", "ValidatingAdmissionPolicyBinding"}:
        state["policy"] = True
        done(manifest)
    fail("unexpected apply kind " + kind)

if args[:2] == ["delete", "statefulset"] and "--dry-run=server" in args:
    if state["policy"]:
        fail("Forbidden: sieve synthetic StatefulSet delete denial")
    done()

if args[:2] == ["delete", "prometheus"]:
    if state["parent"] is None:
        done()
    state["parent"] = None
    if not state["policy"]:
        state["statefulset"] = None
        state["pod"] = None
    done()

if args[:2] == ["delete", "validatingadmissionpolicybinding"]:
    state["policy"] = False
    if state["parent"] and state["statefulset"]:
        current_uid = state["parent"]["metadata"]["uid"]
        owner_uid = state["statefulset"]["metadata"]["ownerReferences"][0]["uid"]
        if owner_uid != current_uid:
            state["pending_replacement"] = 2
    done()

if args[:2] == ["delete", "validatingadmissionpolicy"]:
    done()

if args[:2] == ["get", "prometheus"]:
    done(state["parent"])

if args[:2] == ["get", "statefulset"]:
    if state["pending_replacement"] == 2:
        state["statefulset"] = None
        state["pod"] = None
        state["pending_replacement"] = 1
        done()
    if state["pending_replacement"] == 1:
        create_statefulset()
        state["pending_replacement"] = 0
    done(state["statefulset"])

if args[:2] == ["get", "pods"]:
    done({"items": [state["pod"]] if state["pod"] else []})

fail("unexpected fixture command: " + repr(args))
"""
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "controllers/prometheus-operator/workloads/workload.py"
            ),
            f"owner-handoff-{mode}",
            str(output),
            "--timeout",
            "2",
            "--settle-seconds",
            "0.02",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PROMETHEUS_HANDOFF_STATE": str(tmp_path / "cluster.json"),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("ownership-handoff.json").read_text())
    assert evidence["status"] == "completed"
    assert evidence["semantic_status"] == ("failed" if mode == "rejected" else "passed")
    assert evidence["checks"] == {
        "initial_child_owned_by_initial_parent": True,
        "replacement_parent_has_new_uid": True,
        "handoff_child_owned_by_replacement_parent": mode == "control",
        "handoff_status_rejects_stale_ownership": mode == "control",
        "final_child_owned_by_replacement_parent": True,
    }
    assert evidence["initial"]["parent_uid"] == "prometheus-1"
    assert evidence["handoff"]["parent_uid"] == "prometheus-2"
    if mode == "rejected":
        assert evidence["policy_probe"]["accepted"] is False
        assert evidence["handoff"]["statefulset_uid"] == "statefulset-1"
        assert evidence["handoff"]["statefulset_owner_uid"] == "prometheus-1"
        assert evidence["handoff"]["pod_uid"] == "pod-1"
        assert evidence["handoff"]["available"] is True
        assert evidence["final"]["statefulset_uid"] == "statefulset-2"
    else:
        assert evidence["policy_probe"] is None
        assert evidence["handoff"]["statefulset_uid"] == "statefulset-2"
    assert evidence["final"]["statefulset_owner_uid"] == "prometheus-2"

    command_records = [
        json.loads(line)
        for line in (output.parent / "kubectl-commands/commands.jsonl")
        .read_text()
        .splitlines()
    ]
    assert command_records
    assert all("args" not in record for record in command_records)
    started_records = [
        record for record in command_records if record["status"] == "started"
    ]
    assert started_records
    assert all(record["cwd"] == str(output.parent) for record in started_records)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--timeout", "nan"),
        ("--poll-seconds", "inf"),
        ("--settle-seconds", "nan"),
    ],
)
def test_workload_rejects_non_finite_limits(tmp_path, option, value):
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/prometheus-operator/workloads/workload.py"),
            "owner-handoff-control",
            str(output),
            option,
            value,
        ],
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "finite" in result.stderr
    assert not output.exists()
