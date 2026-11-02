"""Coverage requires observed invocation/delivery ordering, never configured timing."""

import copy
import json

import pytest


@pytest.fixture
def overlap_trace(tmp_path):
    old = {
        "kind": "ExternalSecret",
        "metadata": {
            "namespace": "default",
            "name": "subject",
            "uid": "parent-1",
            "generation": 2,
            "resourceVersion": "11",
            "labels": {"sieve.testing/state": "metadata-1-add"},
        },
        "spec": {"template": {"key": "one"}},
    }
    new = copy.deepcopy(old)
    new["metadata"].update(
        generation=3,
        resourceVersion="14",
        labels={"sieve.testing/state": "metadata-2-change"},
    )
    new["spec"] = {"template": {"key": "two"}}
    lines = [
        "[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\tcall-1\tSIEVE-V2:instance-1\n",
        "[SIEVE-AFTER-CACHE-READ]\tGet\texternalsecret\tdefault\tsubject\toperator.Reconcile\tNoError\t"
        + json.dumps(old)
        + "\tSIEVE-V2:call-1\n",
        "[SIEVE-BEFORE-HEAR]\t7\tUpdated\texternalsecret\t" + json.dumps(new) + "\n",
        "[SIEVE-AFTER-HEAR]\t7\n",
        "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\tcall-1\tSIEVE-V2:instance-1\n",
    ]
    path = tmp_path / "trace.log"
    path.write_text("".join(lines))
    return path, lines, [old, new]


def test_overlap_binds_an_older_read_and_completed_new_delivery_to_one_live_invocation(
    overlap_trace,
):
    from sieve_tool.testing.sequence_coverage import delivery_overlap

    path, _, accepted = overlap_trace
    coverage = delivery_overlap(path, accepted=accepted)
    assert coverage.verified
    assert coverage.witnesses[0]["invocation_id"] == "call-1"
    assert coverage.witnesses[0]["positions"] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize(
    "missing",
    [
        "read",
        "delivery-end",
        "reconcile-end",
        "identity",
        "exact-input",
        "ordering",
        "other-observer",
        "other-invocation",
    ],
)
def test_incomplete_or_unrelated_events_cannot_prove_overlap(overlap_trace, missing):
    from sieve_tool.testing.sequence_coverage import delivery_overlap

    path, lines, accepted = overlap_trace
    if missing == "read":
        lines.pop(1)
    elif missing == "delivery-end":
        lines.pop(3)
    elif missing == "reconcile-end":
        lines.pop()
    elif missing == "identity":
        lines = [
            line.replace("\tSIEVE-V2:instance-1", "").replace("\tSIEVE-V2:call-1", "")
            for line in lines
        ]
    elif missing == "exact-input":
        accepted[-1]["metadata"]["resourceVersion"] = "unobserved"
    elif missing == "ordering":
        lines = lines[:2] + [lines[-1]] + lines[2:4]
    elif missing == "other-observer":
        lines[2] = lines[2].rstrip() + "\tmetadata-informer\n"
    elif missing == "other-invocation":
        lines[1] = lines[1].replace("SIEVE-V2:call-1", "SIEVE-V2:other")
    path.write_text("".join(lines))
    assert not delivery_overlap(path, accepted=accepted).verified


def test_recreated_parent_cannot_supply_overlap_for_one_identity_history(overlap_trace):
    from sieve_tool.testing.sequence_coverage import delivery_overlap

    path, lines, accepted = overlap_trace
    lines[2] = lines[2].replace("parent-1", "replacement-parent")
    accepted[-1]["metadata"]["uid"] = "replacement-parent"
    path.write_text("".join(lines))
    coverage = delivery_overlap(path, accepted=accepted)
    assert not coverage.verified
    assert coverage.reasons == ("invalid_accepted_history",)


def test_coverage_cli_writes_a_hash_bound_separate_verdict_and_preserves_inputs(
    overlap_trace, tmp_path
):
    from sieve_tool.testing.sequence_coverage import main

    path, _, accepted = overlap_trace
    observations = tmp_path / "metadata-observations.json"
    observations.write_text(
        json.dumps(
            {"accepted": [{"role": "subject", "response": obj} for obj in accepted]}
        )
    )
    before = {p.name: p.read_bytes() for p in (path, observations)}
    output = tmp_path / "coverage.json"
    assert (
        main([str(path), "--observations", str(observations), "--output", str(output)])
        == 0
    )
    report = json.loads(output.read_text())
    assert report["verified"] is True
    assert set(report["evidence_sha256"]) == {"trace.log", "metadata-observations.json"}
    assert before == {p.name: p.read_bytes() for p in (path, observations)}
    with pytest.raises(SystemExit) as error:
        main([str(path), "--observations", str(observations), "--output", str(output)])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "diagnostic",
    [
        "[SIEVE-TRACE-ERROR] unmatched invocation",
        "Sieve client RPC error: missing identity",
    ],
)
def test_observer_errors_prevent_an_otherwise_complete_trace_from_certifying_coverage(
    overlap_trace, diagnostic
):
    from sieve_tool.testing.sequence_coverage import delivery_overlap

    path, lines, accepted = overlap_trace
    path.write_text("".join(lines) + diagnostic + "\n")
    coverage = delivery_overlap(path, accepted=accepted)
    assert not coverage.verified
    assert coverage.reasons == ("observer_error",)
