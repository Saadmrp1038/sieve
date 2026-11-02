"""A completed process cannot substitute for a physically verified timing hold."""

import json

import pytest

from sieve_tool.fault_proofs import audit_fault
from sieve_tool.plans import parse_plan
from sieve_tool.results import TestResult as RunResult
from tests.test_sequence_coverage import overlap_trace as overlap_trace


@pytest.fixture
def scheduled_evidence(tmp_path, overlap_trace):
    path, lines, accepted = overlap_trace
    identity = {
        "mode": "hold",
        "resourceKey": "externalsecret/default/subject",
        "reconciler": "operator.Reconcile",
        "invocationID": "call-1",
        "instanceID": "instance-1",
        "uid": "parent-1",
        "generation": 2,
        "resourceVersion": "11",
        "releasedGeneration": 0,
        "releasedResourceVersion": "",
    }
    held = {**identity, "state": "held"}
    released = {
        **identity,
        "state": "released",
        "releasedGeneration": 3,
        "releasedResourceVersion": "14",
    }
    completed = {**released, "state": "completed"}
    marker = lambda value: "[SIEVE-DELIVERY-OVERLAP]\t" + json.dumps(value) + "\n"
    (tmp_path / "sieve-server.log").write_text(
        "".join(lines[:2])
        + marker(held)
        + "".join(lines[2:4])
        + marker(released)
        + lines[4]
        + marker(completed)
    )
    (tmp_path / "metadata-observations.json").write_text(
        json.dumps(
            {
                "accepted": [{"role": "subject", "response": obj} for obj in accepted],
                "delivery_overlap": {"held": held, "completed": completed},
            }
        )
    )
    for name in ("process-outcome.json", "injection-process-outcome.json"):
        (tmp_path / name).write_text('{"status":"completed","returncode":0}')
    plan = parse_plan(
        {
            "workload": "metadata-labels-overlap",
            "actions": [],
            "coverageResources": ["externalsecret/default/subject"],
            "deliveryOverlap": {
                "mode": "hold",
                "resourceKey": "externalsecret/default/subject",
                "reconciler": "operator.Reconcile",
                "holdPhase": "metadata-1-add",
                "releasePhase": "metadata-2-change",
                "timeoutSeconds": 20,
            },
        }
    )
    result = RunResult(True, True, [], [], ["retained raw history alarm"], True, "")
    return tmp_path, plan, result


def test_scheduled_overlap_proof_binds_hold_release_and_end_to_exact_delivered_inputs(
    scheduled_evidence,
):
    directory, plan, result = scheduled_evidence
    proof = audit_fault(directory, plan, result)
    assert proof["physically_verified"] is True, proof["reasons"]
    assert proof["delivery_overlap"]["verified"] is True
    assert proof["raw_alarms"] == ["retained raw history alarm"]
    assert proof["semantic_status"] == "unknown"


def test_hold_receipt_must_match_the_actual_identified_read_version(scheduled_evidence):
    directory, plan, result = scheduled_evidence
    path = directory / "sieve-server.log"
    lines = []
    for line in path.read_text().splitlines():
        if "[SIEVE-DELIVERY-OVERLAP]\t" in line:
            prefix, body = line.split("\t", 1)
            value = json.loads(body)
            value["resourceVersion"] = "unobserved-read-version"
            line = prefix + "\t" + json.dumps(value)
        lines.append(line)
    path.write_text("\n".join(lines) + "\n")
    evidence = directory / "metadata-observations.json"
    data = json.loads(evidence.read_text())
    for key in ("held", "completed"):
        data["delivery_overlap"][key]["resourceVersion"] = "unobserved-read-version"
    evidence.write_text(json.dumps(data))
    proof = audit_fault(directory, plan, result)
    assert proof["physically_verified"] is False


def test_observer_rpc_error_invalidates_an_otherwise_complete_schedule(
    scheduled_evidence,
):
    directory, plan, result = scheduled_evidence
    (directory / "controller.log").write_text(
        "Sieve client RPC error: connection closed\n"
    )
    proof = audit_fault(directory, plan, result)
    assert proof["physically_verified"] is False
    assert "observer" in " ".join(proof["reasons"])


@pytest.mark.parametrize(
    "missing",
    ["receipt-end", "invocation-end", "completed-marker", "held-workload-receipt"],
)
def test_missing_physical_evidence_cannot_become_a_verified_fault(
    scheduled_evidence, missing
):
    directory, plan, result = scheduled_evidence
    path = directory / "sieve-server.log"
    if missing == "held-workload-receipt":
        evidence = directory / "metadata-observations.json"
        data = json.loads(evidence.read_text())
        del data["delivery_overlap"]["held"]
        evidence.write_text(json.dumps(data))
    else:
        marker = {
            "receipt-end": "[SIEVE-AFTER-HEAR]",
            "invocation-end": "[SIEVE-AFTER-RECONCILE]",
            "completed-marker": '"state": "completed"',
        }[missing]
        path.write_text(
            "\n".join(
                line for line in path.read_text().splitlines() if marker not in line
            )
            + "\n"
        )
    proof = audit_fault(directory, plan, result)
    assert proof["physically_verified"] is False
    assert proof["raw_alarms"] == ["retained raw history alarm"]
