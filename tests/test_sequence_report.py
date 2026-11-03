"""Public report counts candidates separately from actual observed executions."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def prepared(tmp_path):
    directory = tmp_path / "prepared"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/external-secrets/workloads/workload.py"),
            "metadata-labels-overlap",
            str(directory / "workload.log"),
            "--plan-only",
        ],
        env={"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return directory


def test_public_report_does_not_count_a_generated_catalog_as_executed_coverage(
    prepared, tmp_path
):
    from sieve_tool.testing.sequence_report import main

    before = {p.name: p.read_bytes() for p in prepared.iterdir()}
    output = tmp_path / "report.json"
    assert main(["--case", str(prepared), "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["status"] == "not_attempted"
    assert result["counts"] == {
        "generated_histories": 8,
        "selected_cases": 1,
        "attempted_executions": 0,
        "accepted_histories": 0,
        "fully_observed_histories": 0,
        "verified_overlap_executions": 0,
        "required_overlap_executions": 0,
        "required_overlap_verified": 0,
        "reported_semantic_passes": 0,
    }
    assert before == {p.name: p.read_bytes() for p in prepared.iterdir()}
    with pytest.raises(SystemExit):
        main(["--case", str(prepared), "--output", str(output)])


@pytest.fixture
def execution(prepared):
    import copy

    states = []
    for index, value in enumerate((None, "one", "two", None)):
        labels = {"sieve.testing/keep": "retained"}
        if value is not None:
            labels["sieve.testing/history"] = value
        parent = {
            "apiVersion": "external-secrets.io/v1",
            "kind": "ExternalSecret",
            "metadata": {
                "name": "sieve-metadata-subject",
                "namespace": "default",
                "uid": "parent-1",
                "generation": index + 1,
                "resourceVersion": str(index + 10),
                "labels": {},
            },
            "spec": {
                "target": {
                    "template": {
                        "metadata": {
                            "labels": labels,
                            "annotations": {"sieve.testing/keep": "retained"},
                        }
                    }
                }
            },
        }
        if index:
            parent["metadata"]["labels"]["sieve.testing/state"] = (
                f"metadata-{index}-{('add', 'change', 'remove')[index - 1]}"
            )
        states.append(parent)
    accepted = [
        {"role": "subject", "operation": op, "response": parent}
        for op, parent in zip(
            ("initial", "add", "change", "remove"), states, strict=True
        )
    ]
    fresh = copy.deepcopy(states[-1])
    fresh["metadata"].update(
        name="fresh", uid="fresh-1", generation=1, resourceVersion="20"
    )
    accepted.append({"role": "fresh", "operation": "fresh", "response": fresh})
    (prepared / "metadata-observations.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "passed",
                "accepted": accepted,
                "samples": [],
            }
        )
    )
    (prepared / "process-outcome.json").write_text(
        json.dumps({"status": "completed", "returncode": 0})
    )
    (prepared / "semantic-outcome.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "checks": [
                    {"name": "metadata_history_and_fresh_state_stable", "passed": True}
                ],
            }
        )
    )
    (prepared / "workload.log").write_text("FINISH-SIEVE-TEST\n")

    def boundary(kind, identity):
        return f"[SIEVE-{kind}-RECONCILE]\toperator.Reconcile\t{identity}\tSIEVE-V2:instance-1\n"

    def read(index, identity):
        return (
            "[SIEVE-AFTER-CACHE-READ]\tGet\texternalsecret\tdefault\tsieve-metadata-subject\toperator.Reconcile\tNoError\t"
            + json.dumps(states[index])
            + "\tSIEVE-V2:"
            + identity
            + "\n"
        )

    lines = [
        boundary("BEFORE", "call-1"),
        read(0, "call-1"),
        boundary("AFTER", "call-1"),
        boundary("BEFORE", "call-2"),
        read(1, "call-2"),
        "[SIEVE-BEFORE-HEAR]\t1\tUpdated\texternalsecret\t"
        + json.dumps(states[2])
        + "\n",
        "[SIEVE-AFTER-HEAR]\t1\n",
        read(2, "call-2"),
        boundary("AFTER", "call-2"),
        boundary("BEFORE", "call-3"),
        read(3, "call-3"),
        boundary("AFTER", "call-3"),
    ]
    (prepared / "sieve-server.log").write_text("".join(lines))
    return prepared


def test_report_counts_repeated_executions_separately_from_unique_histories_and_measured_coverage(
    execution, tmp_path
):
    import shutil

    from sieve_tool.testing.sequence_report import main

    second = tmp_path / "second"
    shutil.copytree(execution, second)
    output = tmp_path / "report.json"
    assert (
        main(
            [
                "--case",
                str(execution),
                "--case",
                str(second),
                "--case",
                str(execution),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text())
    assert result["status"] == "satisfied"
    assert result["counts"] == {
        "generated_histories": 8,
        "selected_cases": 1,
        "attempted_executions": 2,
        "accepted_histories": 2,
        "fully_observed_histories": 2,
        "verified_overlap_executions": 2,
        "required_overlap_executions": 2,
        "required_overlap_verified": 2,
        "reported_semantic_passes": 2,
    }
    assert all(
        row["observed_inputs"] == [0, 1, 2, 3] and row["delivered_inputs"] == [2]
        for row in result["cases"]
    )


@pytest.mark.parametrize(
    "missing",
    [
        "metadata-plan.json",
        "metadata-observations.json",
        "sieve-server.log",
        "process-outcome.json",
    ],
)
def test_incomplete_attempts_remain_counted_and_cannot_pass(
    execution, tmp_path, missing
):
    from sieve_tool.testing.sequence_report import main

    (execution / missing).unlink()
    output = tmp_path / "incomplete.json"
    assert main(["--case", str(execution), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["status"] == "incomplete"
    assert report["counts"]["attempted_executions"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 1.0},
        {"schedule": "guess"},
        {"generated": []},
        {
            "selected": {
                "channel": "labels",
                "steps": [{"operation": "add", "value": "unplanned"}],
            }
        },
    ],
)
def test_invalid_catalog_or_schedule_cannot_inflate_coverage(
    execution, tmp_path, change
):
    from sieve_tool.testing.sequence_report import main

    path = execution / "metadata-plan.json"
    plan = json.loads(path.read_text())
    plan.update(change)
    path.write_text(json.dumps(plan))
    output = tmp_path / "invalid.json"
    main(["--case", str(execution), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["status"] == "incomplete"
    assert report["counts"]["generated_histories"] == 0
    assert report["counts"]["attempted_executions"] == 1


def test_controller_observer_error_invalidates_derived_coverage_without_losing_attempt(
    execution, tmp_path
):
    from sieve_tool.testing.sequence_report import main

    (execution / "controller.log").write_text(
        "Sieve client RPC error: disconnected observer\n"
    )
    output = tmp_path / "observer-error.json"
    main(["--case", str(execution), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["status"] == "incomplete"
    assert report["counts"]["attempted_executions"] == 1
    assert report["counts"]["verified_overlap_executions"] == 0
    assert report["counts"]["fully_observed_histories"] == 0


def test_selected_but_unattempted_case_cannot_disappear_behind_a_successful_case(
    execution, tmp_path
):
    from sieve_tool.testing.sequence_report import main

    prepared = tmp_path / "unattempted"
    prepared.mkdir()
    plan = json.loads((execution / "metadata-plan.json").read_text())
    plan["schedule"] = "settled"
    (prepared / "metadata-plan.json").write_text(json.dumps(plan))
    output = tmp_path / "partial-campaign.json"
    main(["--case", str(execution), "--case", str(prepared), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["counts"]["selected_cases"] == 2
    assert report["counts"]["attempted_executions"] == 1
    assert report["status"] == "incomplete"


@pytest.mark.parametrize("root", [None, [], "not an object"])
def test_nonobject_plan_remains_an_incomplete_attempt(execution, tmp_path, root):
    from sieve_tool.testing.sequence_report import main

    (execution / "metadata-plan.json").write_text(json.dumps(root))
    output = tmp_path / "bad-root.json"
    main(["--case", str(execution), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["status"] == "incomplete"
    assert report["counts"]["attempted_executions"] == 1


@pytest.mark.parametrize("field,value", [("uid", 0), ("generation", 0)])
def test_invalid_api_identity_does_not_count_as_an_accepted_history(
    execution, tmp_path, field, value
):
    from sieve_tool.testing.sequence_report import main

    path = execution / "metadata-observations.json"
    observations = json.loads(path.read_text())
    for index, row in enumerate(
        r for r in observations["accepted"] if r["role"] == "subject"
    ):
        row["response"]["metadata"][field] = index if field == "generation" else value
    path.write_text(json.dumps(observations))
    output = tmp_path / "bad-input.json"
    main(["--case", str(execution), "--output", str(output)])
    report = json.loads(output.read_text())
    assert report["counts"]["accepted_histories"] == 0
    assert report["status"] == "incomplete"
