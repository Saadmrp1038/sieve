"""Behavior of workload commands against a controlled Kubernetes boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def kubectl_boundary(tmp_path):
    state = tmp_path / "cluster.json"
    state.write_text(json.dumps({"scenario": "repair", "parent": None, "child": None}))
    command = tmp_path / "kubectl"
    command.write_text(
        f"#!{sys.executable}\n"
        + """import base64
import copy
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["REPAIR_TEST_CLUSTER"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if args[:2] == ["--namespace", "default"]:
    args = args[2:]
verb = args[0]
if verb == "apply":
    if state["scenario"] == "startup" and not state.get("workers_ready"):
        raise SystemExit("informer workers are not ready")
    parent = json.load(sys.stdin)
    parent["metadata"].update(uid="parent-1", generation=1)
    parent["status"] = {"secret": {"uid": "child-1"}}
    state["parent"] = parent
    state["child"] = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "sieve-generated-secret", "namespace": "default",
                     "uid": "child-1", "ownerReferences": [
                         {"uid": "parent-1", "kind": "StringSecret", "controller": True}]},
        "data": {"marker": base64.b64encode(b"initial").decode(),
                 "password": base64.b64encode(b"0123456789abcdef").decode()}}
    result = parent
elif verb == "logs":
    state["workers_ready"] = True
    path.write_text(json.dumps(state))
    for name in ("stringsecret-controller", "secret-controller"):
        print(json.dumps({"msg": "Starting workers", "controller": name}))
    raise SystemExit(0)
elif verb == "get":
    if args[1] == "secret" and state.pop("restore_marker", False):
        state["child"]["data"]["marker"] = base64.b64encode(b"initial").decode()
    result = state["parent" if args[1] == "stringsecret" else "child"]
elif verb == "patch" and args[1] == "secret":
    patch = json.loads(args[args.index("-p") + 1])
    if state["scenario"] != "no-mutation":
        state["child"]["data"].update(patch["data"])
    result = copy.deepcopy(state["child"])
    state["restore_marker"] = state["scenario"] == "repair"
elif verb == "delete" and args[1] == "secret":
    result = copy.deepcopy(state["child"])
    state["child"]["metadata"]["uid"] = "child-2"
    state["parent"]["status"]["secret"]["uid"] = "child-2"
    if state["scenario"] == "no-repair":
        state["child"] = None
    if state["scenario"] == "parent-changed":
        state["parent"]["metadata"]["generation"] = 2
    if state["scenario"] == "same-child":
        state["child"]["metadata"]["uid"] = "child-1"
        state["parent"]["status"]["secret"]["uid"] = "child-1"
    if state["scenario"] == "foreign-owner":
        state["child"]["metadata"]["ownerReferences"][0]["kind"] = "OtherKind"
else:
    raise SystemExit("unsupported test API command: " + repr(args))
path.write_text(json.dumps(state))
print(json.dumps(result) if result is not None else "")
"""
    )
    command.chmod(0o755)
    return {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "REPAIR_TEST_CLUSTER": str(state),
    }


def run_workload(tmp_path, environment, workload="delete-child"):
    output = tmp_path / "result" / "workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/kubernetes-secret-generator/workloads/workload.py"),
            workload,
            str(output),
            "--repair-timeout",
            "2",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    evidence = output.with_name("repair-observations.json")
    return (
        result,
        output,
        json.loads(evidence.read_text()) if evidence.exists() else None,
    )


def test_deleted_secret_is_replaced_under_the_unchanged_parent(
    tmp_path, kubectl_boundary
):
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["status"] == "repaired"
    assert evidence["parent_before"]["metadata"]["uid"] == "parent-1"
    assert evidence["parent_after"]["metadata"]["uid"] == "parent-1"
    assert evidence["parent_after"]["metadata"]["generation"] == 1
    assert evidence["child_before"]["metadata"]["uid"] == "child-1"
    assert evidence["mutation_response"]["metadata"]["uid"] == "child-1"
    assert evidence["child_after"]["metadata"]["uid"] == "child-2"
    assert evidence["parent_after"]["status"]["secret"]["uid"] == "child-2"


def test_missing_repair_fails_and_preserves_the_observed_state(
    tmp_path, kubectl_boundary
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-repair", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "repair_timeout"
    assert evidence["parent_after"]["metadata"]["uid"] == "parent-1"
    assert evidence["child_after"] is None


def test_secret_data_drift_is_observed_then_repaired(tmp_path, kubectl_boundary):
    result, output, evidence = run_workload(tmp_path, kubectl_boundary, "drift-child")
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["status"] == "repaired"
    assert evidence["mutation_response"]["data"]["marker"] == "ZHJpZnQ="
    assert evidence["child_after"]["data"]["marker"] == "aW5pdGlhbA=="
    assert evidence["parent_after"]["spec"] == evidence["parent_before"]["spec"]


def test_repair_cannot_pass_after_the_parent_changed(tmp_path, kubectl_boundary):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "parent-changed", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "parent_changed"


def test_delete_requires_a_new_child_identity(tmp_path, kubectl_boundary):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "same-child", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "repair_timeout"


def test_drift_cannot_pass_without_an_observed_mutation(tmp_path, kubectl_boundary):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-mutation", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary, "drift-child")
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "mutation_failed"


def test_workload_waits_for_informer_workers_before_creating_the_parent(
    tmp_path, kubectl_boundary
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(json.dumps({"scenario": "startup", "parent": None, "child": None}))
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence["status"] == "repaired"


def test_matching_uid_from_another_owner_kind_is_not_repair(tmp_path, kubectl_boundary):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "foreign-owner", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary)
    assert result.returncode == 1
    assert evidence["status"] == "repair_timeout"
