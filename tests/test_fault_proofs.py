"""Saved results distinguish physical faults from coordinator completion."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sieve_tool import cli
from sieve_tool.oracle.artifacts import ArtifactError
from sieve_tool.plans import load_plan
from sieve_tool.results import TestResult as RunResult
from sieve_tool.runtime.runner import save_run_result

ROOT = Path(__file__).resolve().parents[1]


def save_case(
    tmp_path,
    *,
    deleted=False,
    semantic=None,
    injection_json=None,
    existing_replica=False,
    replacement_namespace="default",
):
    plan = ROOT / "fixtures/plans/restart.yaml"
    label = load_plan(plan).actions[0].controller_label
    directory = tmp_path / "case"
    directory.mkdir()
    (directory / "sieve-server.log").write_text(
        "controller pod gets deleted\ncontroller pod 0 ready now\n"
        "Sieve test coordinator finishes all actions\n"
    )
    for name in ("process-outcome.json", "injection-process-outcome.json"):
        (directory / name).write_text('{"status":"completed","returncode":0}')
    if injection_json is not None:
        (directory / "injection-process-outcome.json").write_text(injection_json)
    (directory / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    events = []
    for uid in ("original", "replacement"):
        namespace = "default" if uid == "original" else replacement_namespace
        obj = {
            "metadata": {
                "name": "controller-" + uid,
                "namespace": namespace,
                "uid": uid,
                "labels": {"sievetag": label},
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        events.append(
            "\t".join(
                [
                    "[SIEVE-API-EVENT]",
                    "ADDED",
                    "unused",
                    "pod",
                    namespace,
                    "controller-" + uid,
                    json.dumps(obj),
                ]
            )
        )
        if deleted and uid == "original":
            events.append(events[-1].replace("\tADDED\t", "\tDELETED\t"))
    if existing_replica:
        # Replica B existed before A was deleted; a later Ready update is not
        # evidence of a replacement being created by this restart.
        original_add, original_delete, replica_add = events
        events = [
            original_add,
            replica_add,
            original_delete,
            replica_add.replace("\tADDED\t", "\tMODIFIED\t"),
        ]
    (directory / "apiserver1.log").write_text("\n".join(events) + "\n")
    context = SimpleNamespace(
        mode="test",
        controller="operator",
        test_workload="workload",
        original_test_plan=str(plan),
        result_dir=str(directory),
        result_root_dir=str(tmp_path),
    )
    if semantic is not None:
        (directory / "semantic-outcome.json").write_text(json.dumps(semantic))
    raw = RunResult(True, True, [], [], ["retained history alarm"], True, "")
    save_run_result(context, raw, 0)
    return json.loads((directory / "fault-proof.json").read_text())


def test_saved_restart_requires_api_deletion_not_just_distinct_ready_pods(tmp_path):
    proof = save_case(tmp_path)
    assert proof["physically_verified"] is False
    assert "API deletion" in " ".join(proof["reasons"])
    assert proof["raw_alarms"] == ["retained history alarm"]
    assert proof["semantic_status"] == "unknown"


@pytest.mark.parametrize("healthy", [True, False])
def test_explicit_semantic_checks_are_separate_from_retained_history_alarms(
    tmp_path, healthy
):
    proof = save_case(
        tmp_path,
        deleted=True,
        semantic={
            "schema_version": 1,
            "checks": [{"name": "acknowledged SQL rows preserved", "passed": healthy}],
        },
    )
    assert proof["physically_verified"] is True
    assert proof["semantic_status"] == ("passed" if healthy else "failed")
    assert proof["semantic_checks"][0]["passed"] is healthy
    assert proof["raw_alarms"] == ["retained history alarm"]


def test_report_counts_verified_cases_and_preserves_history_alarms(tmp_path, capsys):
    save_case(
        tmp_path,
        deleted=True,
        semantic={
            "schema_version": 1,
            "checks": [{"name": "application data preserved", "passed": True}],
        },
    )
    assert (
        cli.main(
            ["--repo", str(ROOT), "report", "--results", str(tmp_path), "--proofs"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["attempted"] == 1
    assert report["physically_verified"] == 1
    assert report["semantic_passed"] == 1
    assert report["raw_alarm_count"] == 1


def test_report_rejects_a_proof_after_source_evidence_changes(tmp_path, capsys):
    save_case(
        tmp_path,
        deleted=True,
        semantic={
            "schema_version": 1,
            "checks": [{"name": "application data preserved", "passed": True}],
        },
    )
    (tmp_path / "case/apiserver1.log").write_text("")
    assert (
        cli.main(
            ["--repo", str(ROOT), "report", "--results", str(tmp_path), "--proofs"]
        )
        == 1
    )
    assert "evidence hash" in capsys.readouterr().err


@pytest.mark.parametrize(
    "invalid",
    [
        '{"status":"completed","returncode":false}',
        '{"status":"failed","status":"completed","returncode":0}',
    ],
)
def test_malformed_process_evidence_cannot_verify_a_fault(tmp_path, invalid):
    with pytest.raises(ArtifactError):
        save_case(tmp_path, deleted=True, injection_json=invalid)


def test_existing_ready_replica_is_not_a_new_replacement(tmp_path):
    proof = save_case(tmp_path, deleted=True, existing_replica=True)
    assert proof["physically_verified"] is False
    assert "replacement" in " ".join(proof["reasons"])


def test_another_namespace_cannot_supply_the_replacement(tmp_path):
    assert (
        save_case(tmp_path, deleted=True, replacement_namespace="other")[
            "physically_verified"
        ]
        is False
    )
