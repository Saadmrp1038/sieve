"""Behavior of workload commands against a controlled Kubernetes boundary."""

import base64
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
import time
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
    if parent["kind"] in ("SecretStore", "Issuer"):
        parent["status"] = {"conditions": [{"type": "Ready", "status": "True"}]}
        state["store"] = parent
        path.write_text(json.dumps(state))
        print(json.dumps(parent))
        raise SystemExit(0)
    parent["metadata"].update(uid="parent-1", generation=1)
    parent["status"] = {"secret": {"uid": "child-1"}}
    state["parent"] = parent
    state["child"] = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "sieve-generated-secret", "namespace": "default",
                     "uid": "child-1", "ownerReferences": [
                         {"uid": "parent-1", "name": "sieve-generated-secret", "kind": "StringSecret", "controller": True}]},
        "data": {"marker": base64.b64encode(b"initial").decode(),
                 "password": base64.b64encode(b"0123456789abcdef").decode()}}
    if parent["kind"] == "RabbitmqCluster":
        state["child"] = {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": "rabbitmq-cluster", "namespace": "default",
                         "uid": "child-1", "ownerReferences": [
                             {"uid": "parent-1", "name": "rabbitmq-cluster", "kind": "RabbitmqCluster", "controller": True}]},
            "spec": {"selector": {"app.kubernetes.io/name": "rabbitmq-cluster"},
                     "ports": [{"port": 5672, "name": "amqp"}]}}
    if parent["kind"] == "ExternalSecret":
        parent["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
        state["child"]["metadata"]["name"] = "sieve-external-secret"
        state["child"]["metadata"]["ownerReferences"] = [
            {"uid": "parent-1", "name": "sieve-external-secret", "kind": "ExternalSecret", "controller": True}]
        state["child"]["data"] = {"marker": base64.b64encode(b"expected-value").decode()}
    if parent["kind"] == "Certificate":
        parent["status"]["conditions"] = [{"type": "Ready", "status": "True", "observedGeneration": 1}]
        state["child"]["metadata"]["name"] = "sieve-certificate"
        state["child"]["metadata"]["ownerReferences"] = [
            {"uid": "parent-1", "name": "sieve-certificate", "kind": "Certificate", "controller": True}]
        state["child"]["data"] = json.loads(Path(os.environ["REPAIR_TEST_TLS"]).read_text())
    result = parent
elif verb == "logs":
    state["workers_ready"] = True
    path.write_text(json.dumps(state))
    for name in ("stringsecret-controller", "secret-controller"):
        print(json.dumps({"msg": "Starting workers", "controller": name}))
    raise SystemExit(0)
elif verb == "get":
    if args[1] in ("secretstore", "issuer"):
        print(json.dumps(state["store"]))
        raise SystemExit(0)
    if args[1] == "secret" and state["scenario"] == "transient-repair" and state.get("disturbed_at"):
        if time.monotonic() - state["disturbed_at"] > 0.3:
            state["child"]["data"]["marker"] = base64.b64encode(b"drift").decode()
    if args[1] == "endpoints":
        if state["scenario"] == "no-endpoints" and state["child"]["metadata"]["uid"] == "child-2":
            print(json.dumps({"subsets": []}))
            raise SystemExit(0)
        print(json.dumps({"subsets": [{"addresses": [{"ip": "10.0.0.7"}],
                                      "ports": [{"port": 5672}]}]}))
        raise SystemExit(0)
    if args[1] == "secret" and state.pop("restore_marker", False):
        if state["parent"]["kind"] == "Certificate":
            state["child"]["data"] = json.loads(Path(os.environ["REPAIR_TEST_TLS"]).read_text())
        else:
            value = b"expected-value" if state["parent"]["kind"] == "ExternalSecret" else b"initial"
            state["child"]["data"]["marker"] = base64.b64encode(value).decode()
    if args[1] == "service" and state.pop("restore_selector", False):
        state["child"]["spec"]["selector"] = {"app.kubernetes.io/name": "rabbitmq-cluster"}
    result = state["parent" if args[1] in ("stringsecret", "rabbitmqcluster", "externalsecret", "certificate") else "child"]
elif verb == "patch" and args[1] == "secret":
    patch = json.loads(args[args.index("-p") + 1])
    if isinstance(patch, list):
        assert patch[0] == {"op": "test", "path": "/metadata/uid",
                            "value": state["child"]["metadata"]["uid"]}
        assert patch[1]["op"] == "replace"
        data = {patch[1]["path"].split("/")[-1]: patch[1]["value"]}
    else:
        data = patch["data"]
    if state["scenario"] != "no-mutation":
        state["child"]["data"].update(data)
    result = copy.deepcopy(state["child"])
    state["restore_marker"] = state["scenario"] == "repair"
elif verb == "patch" and args[1] == "service":
    patch = json.loads(args[args.index("-p") + 1])
    if state["scenario"] != "no-mutation":
        state["child"]["spec"]["selector"] = patch[0]["value"]
    result = copy.deepcopy(state["child"])
    state["restore_selector"] = state["scenario"] == "repair"
elif verb == "delete":
    if "-o" in args and args[args.index("-o") + 1] != "name":
        raise SystemExit("unexpected -o output mode; kubectl delete supports only name")
    assert args[1] == "--raw"
    options = json.load(sys.stdin)
    if options["preconditions"]["uid"] != state["child"]["metadata"]["uid"]:
        raise SystemExit("UID precondition failed")
    result = {"apiVersion": "v1", "kind": "Status", "status": "Success",
              "details": {"uid": state["child"]["metadata"]["uid"]}}
    state["child"]["metadata"]["uid"] = "child-2"
    state["disturbed_at"] = time.monotonic()
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
    if state["scenario"] == "stale-ready":
        state["parent"]["status"]["conditions"][0]["observedGeneration"] = 0
    if state["scenario"] == "key-mismatch":
        other_key = Path(os.environ["REPAIR_TEST_TLS"]).with_name("other-key.pem")
        state["child"]["data"]["tls.key"] = base64.b64encode(other_key.read_bytes()).decode()
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


def run_workload(
    tmp_path,
    environment,
    workload="delete-child",
    controller="kubernetes-secret-generator",
    settle=0,
):
    output = tmp_path / "result" / "workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers" / controller / "workloads/workload.py"),
            workload,
            str(output),
            "--repair-timeout",
            "2",
            "--settle-seconds",
            str(settle),
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
    assert evidence["mutation_response"]["details"]["uid"] == "child-1"
    assert evidence["child_after"]["metadata"]["uid"] == "child-2"
    assert evidence["parent_after"]["status"]["secret"]["uid"] == "child-2"


@pytest.mark.parametrize(
    "controller", ["kubernetes-secret-generator", "rabbitmq-operator"]
)
def test_missing_repair_fails_and_preserves_the_observed_state(
    tmp_path, kubectl_boundary, controller
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-repair", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, controller=controller
    )
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


@pytest.mark.parametrize(
    "controller", ["kubernetes-secret-generator", "rabbitmq-operator"]
)
def test_repair_cannot_pass_after_the_parent_changed(
    tmp_path, kubectl_boundary, controller
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "parent-changed", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, controller=controller
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "parent_changed"


@pytest.mark.parametrize(
    "controller", ["kubernetes-secret-generator", "rabbitmq-operator"]
)
def test_delete_requires_a_new_child_identity(tmp_path, kubectl_boundary, controller):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "same-child", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, controller=controller
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "repair_timeout"


@pytest.mark.parametrize(
    "controller", ["kubernetes-secret-generator", "rabbitmq-operator"]
)
def test_drift_cannot_pass_without_an_observed_mutation(
    tmp_path, kubectl_boundary, controller
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-mutation", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, "drift-child", controller
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "mutation_failed"
    assert evidence["mutation_response"]["metadata"]["uid"] == "child-1"


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


def test_deleted_rabbitmq_service_is_replaced_with_ready_endpoints(
    tmp_path, kubectl_boundary
):
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, controller="rabbitmq-operator"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["status"] == "repaired"
    assert evidence["child_before"]["metadata"]["uid"] == "child-1"
    assert evidence["child_after"]["metadata"]["uid"] == "child-2"
    assert evidence["child_after"]["spec"]["selector"] == {
        "app.kubernetes.io/name": "rabbitmq-cluster"
    }
    assert evidence["endpoints_after"]["subsets"][0]["addresses"] == [
        {"ip": "10.0.0.7"}
    ]


def test_rabbitmq_service_selector_drift_is_observed_then_repaired(
    tmp_path, kubectl_boundary
):
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, "drift-child", "rabbitmq-operator"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence["mutation_response"]["spec"]["selector"] == {
        "sieve-test-invalid": "true"
    }
    assert evidence["child_after"]["spec"]["selector"] == {
        "app.kubernetes.io/name": "rabbitmq-cluster"
    }
    assert evidence["parent_after"]["spec"] == evidence["parent_before"]["spec"]


def test_recreated_service_without_ready_endpoints_is_not_success(
    tmp_path, kubectl_boundary
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-endpoints", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, controller="rabbitmq-operator"
    )
    assert result.returncode == 1
    assert evidence["status"] == "repair_timeout"
    assert evidence["endpoints_after"] == {"subsets": []}


@pytest.mark.parametrize(
    "controller", ["kubernetes-secret-generator", "rabbitmq-operator"]
)
def test_unrepaired_field_drift_fails_with_the_mutated_child_preserved(
    tmp_path, kubectl_boundary, controller
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "no-repair", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(
        tmp_path, kubectl_boundary, "drift-child", controller
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "repair_timeout"
    assert evidence["child_after"] == evidence["mutation_response"]


def test_repair_must_remain_consistent_through_the_settling_period(
    tmp_path, kubectl_boundary
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "transient-repair", "parent": None, "child": None})
    )
    result, output, evidence = run_workload(tmp_path, kubectl_boundary, settle=0.8)
    assert result.returncode == 1
    assert evidence["status"] == "repair_lost"
    assert "FINISH-SIEVE-TEST" not in output.read_text()


def run_current_workload(tmp_path, environment, operator, workload="delete-child"):
    output = tmp_path / operator / "workload.log"
    command = (
        [str(ROOT / "controllers/external-secrets/workloads/workload.py")]
        if operator == "external-secrets"
        else [str(ROOT / "experiments/active-operators/workload.py"), operator]
    )
    result = subprocess.run(
        [
            sys.executable,
            *command,
            workload,
            str(output),
            "--repair-timeout",
            "2",
            "--settle-seconds",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    evidence_path = output.with_name("repair-observations.json")
    evidence = json.loads(evidence_path.read_text()) if evidence_path.exists() else None
    return result, output, evidence


def test_current_external_secrets_recreates_an_owned_secret(tmp_path, kubectl_boundary):
    result, output, evidence = run_current_workload(
        tmp_path, kubectl_boundary, "external-secrets"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["child_after"]["metadata"]["uid"] == "child-2"
    assert evidence["child_after"]["data"] == {"marker": "ZXhwZWN0ZWQtdmFsdWU="}
    assert evidence["parent_after"]["spec"] == evidence["parent_before"]["spec"]


def test_current_external_secrets_repairs_observed_data_drift(
    tmp_path, kubectl_boundary
):
    result, output, evidence = run_current_workload(
        tmp_path, kubectl_boundary, "external-secrets", "drift-child"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["mutation_response"]["data"]["marker"] == "ZHJpZnQ="
    assert evidence["child_after"]["data"]["marker"] == "ZXhwZWN0ZWQtdmFsdWU="
    assert evidence["parent_after"]["spec"] == evidence["parent_before"]["spec"]


@pytest.mark.parametrize(
    ("scenario", "status"),
    [("no-repair", "repair_timeout"), ("no-mutation", "mutation_failed")],
)
def test_current_external_secrets_drift_requires_real_repair(
    tmp_path, kubectl_boundary, scenario, status
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(json.dumps({"scenario": scenario, "parent": None, "child": None}))
    result, output, evidence = run_current_workload(
        tmp_path, kubectl_boundary, "external-secrets", "drift-child"
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == status


@pytest.fixture
def issued_certificate(tmp_path):
    certificate, key = tmp_path / "certificate.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=managed-child.example.test",
            "-addext",
            "subjectAltName=DNS:managed-child.example.test",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    data = tmp_path / "tls.json"
    data.write_text(
        json.dumps(
            {
                "tls.crt": base64.b64encode(certificate.read_bytes()).decode(),
                "tls.key": base64.b64encode(key.read_bytes()).decode(),
            }
        )
    )
    return data


def test_current_cert_manager_recreates_a_usable_certificate(
    tmp_path, kubectl_boundary, issued_certificate
):
    result, output, evidence = run_current_workload(
        tmp_path,
        {**kubectl_boundary, "REPAIR_TEST_TLS": str(issued_certificate)},
        "cert-manager",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    assert evidence["child_after"]["metadata"]["uid"] == "child-2"
    assert evidence["parent_before"]["spec"] == evidence["parent_after"]["spec"]


def test_current_cert_manager_rejects_stale_ready_status(
    tmp_path, kubectl_boundary, issued_certificate
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "stale-ready", "parent": None, "child": None})
    )
    result, output, evidence = run_current_workload(
        tmp_path,
        {**kubectl_boundary, "REPAIR_TEST_TLS": str(issued_certificate)},
        "cert-manager",
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert evidence["status"] == "repair_timeout"


@pytest.mark.parametrize(
    ("scenario", "status", "returncode"),
    [("repair", "repaired", 0), ("no-repair", "repair_timeout", 1)],
)
def test_current_cert_manager_checks_certificate_content_after_drift(
    tmp_path, kubectl_boundary, issued_certificate, scenario, status, returncode
):
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(json.dumps({"scenario": scenario, "parent": None, "child": None}))
    result, output, evidence = run_current_workload(
        tmp_path,
        {**kubectl_boundary, "REPAIR_TEST_TLS": str(issued_certificate)},
        "cert-manager",
        "drift-child",
    )
    assert result.returncode == returncode, result.stdout + result.stderr
    assert evidence["status"] == status
    assert evidence["mutation_response"]["data"]["tls.crt"] == "ZHJpZnQ="
    assert ("FINISH-SIEVE-TEST" in output.read_text()) == (returncode == 0)


def test_current_cert_manager_requires_certificate_and_key_to_match(
    tmp_path, kubectl_boundary, issued_certificate
):
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(issued_certificate.with_name("other-key.pem")),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )
    state = Path(kubectl_boundary["REPAIR_TEST_CLUSTER"])
    state.write_text(
        json.dumps({"scenario": "key-mismatch", "parent": None, "child": None})
    )
    result, output, evidence = run_current_workload(
        tmp_path,
        {**kubectl_boundary, "REPAIR_TEST_TLS": str(issued_certificate)},
        "cert-manager",
    )
    assert result.returncode == 1
    assert evidence["status"] == "repair_timeout"
    assert "FINISH-SIEVE-TEST" not in output.read_text()
