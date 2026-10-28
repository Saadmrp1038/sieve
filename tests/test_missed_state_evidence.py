"""A completed pause is effective only when the returned read skips its input."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sieve_tool.oracle.artifacts import ArtifactError
from sieve_tool.oracle.oracle import validate_run
from sieve_tool.trace import TraceError

ROOT = Path(__file__).resolve().parents[1]
KEY = "secret/default/example"
FRAME = "fixture.Reconcile"


def evidence(tmp_path, case="later"):
    def obj(phase):
        return {
            "kind": "Secret",
            "metadata": {
                "name": "example",
                "namespace": "default",
                "uid": "live-child",
                "resourceVersion": str("ABCD".index(phase) + 1),
                "labels": {"sieve.testing/state": phase},
            },
        }

    source = {
        "schema_version": 1,
        "status": "completed",
        "workload": "independent-updates",
        "resource_key": KEY,
        "updates": [
            {
                "producer": "workload",
                "state": {"metadata": {"labels": {"sieve.testing/state": p}}},
                "response": obj(p),
            }
            for p in "ABCD"
        ],
    }
    (tmp_path / "workload-updates.json").write_text(json.dumps(source))
    plan = yaml.safe_load((ROOT / "fixtures/plans/independent-state.yaml").read_text())
    plan["actions"][0]["pauseBy"] = FRAME
    lines = []

    def receipt(phase, observer):
        value = obj(phase)
        if case == "unaccepted_receipt" and phase == "B":
            value["metadata"]["resourceVersion"] = "unrecorded"
        lines.append(
            "file.go:1: NotifyTestAfterControllerRecv\tUpdated\t"
            + KEY
            + "\t"
            + json.dumps(value)
            + "\t"
            + observer
        )

    for observer in ("metadata-informer", "informer"):
        receipt("B", observer)
    lines.append("file.go:1: Pause controller at beforeControllerRead " + KEY)
    receipt("C", "metadata-informer")
    if case != "missing_cache":
        receipt("C", "informer")
    lines.append("file.go:1: End pause controller at beforeControllerRead " + KEY)
    if case != "missing_read":
        value = obj("B" if case == "intermediate" else "C")
        if case == "unaccepted_return":
            value["metadata"]["resourceVersion"] = "unrecorded"
        lines.append(
            "\t".join(
                [
                    "[SIEVE-AFTER-CACHE-READ]",
                    "Get",
                    "secret",
                    "default",
                    "example",
                    FRAME,
                    "NoError",
                    json.dumps(value),
                ]
            )
        )
    if case != "missing_after_hook":
        lines.append(
            "file.go:1: NotifyTestAfterControllerReadPause\tGet\t" + KEY + "\t"
        )
    lines.append("Sieve test coordinator finishes all actions")
    (tmp_path / "sieve-server.log").write_text("\n".join(lines) + "\n")
    (tmp_path / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (tmp_path / "process-outcome.json").write_text(
        '{"status":"completed","returncode":0}'
    )
    context = SimpleNamespace(result_dir=str(tmp_path), test_plan_content=plan)
    return context


@pytest.mark.parametrize(
    "case, expected",
    [
        ("later", True),
        ("intermediate", False),
        ("missing_read", False),
        ("missing_cache", False),
        ("missing_after_hook", False),
        ("unaccepted_receipt", False),
        ("unaccepted_return", False),
    ],
)
def test_verdict_requires_an_observed_overwrite_and_returned_later_state(
    tmp_path, case, expected
):
    context = evidence(tmp_path, case)
    assert validate_run(context, require_missed_state=True) == (expected, True)
    proof = json.loads((tmp_path / "missed-state-proof.json").read_text())
    assert proof["verified"] is expected


def test_invalid_independent_input_is_a_harness_error(tmp_path):
    context = evidence(tmp_path)
    (tmp_path / "workload-updates.json").write_text('{"schema_version":1}')
    with pytest.raises(ArtifactError, match="workload-updates.json"):
        validate_run(context, require_missed_state=True)


def test_invalid_selected_read_is_a_harness_error(tmp_path):
    context = evidence(tmp_path)
    log = tmp_path / "sieve-server.log"
    log.write_text(log.read_text() + "[SIEVE-AFTER-CACHE-READ]\tGet\n")
    with pytest.raises(TraceError, match="sieve-server.log"):
        validate_run(context, require_missed_state=True)


def test_invalid_selected_receipt_is_a_harness_error(tmp_path):
    context = evidence(tmp_path)
    log = tmp_path / "sieve-server.log"
    lines = log.read_text().splitlines()
    fields = lines[0].split("\t")
    fields[3] = "{invalid"
    lines[0] = "\t".join(fields)
    log.write_text("\n".join(lines) + "\n")
    with pytest.raises(ArtifactError, match=r"sieve-server.log:1:"):
        validate_run(context, require_missed_state=True)


def test_normal_saved_result_includes_strict_missed_state_proof(tmp_path):
    from sieve_tool.results import TestResult as RunResult
    from sieve_tool.runtime.runner import save_run_result

    context = evidence(tmp_path)
    plan = tmp_path / "selected.yaml"
    plan.write_text(yaml.safe_dump(context.test_plan_content))
    (tmp_path / "injection-process-outcome.json").write_text(
        '{"status":"completed","returncode":0}'
    )
    context.mode = "test"
    context.controller = "operator"
    context.test_workload = "independent-updates"
    context.original_test_plan = str(plan)
    context.result_root_dir = str(tmp_path)
    save_run_result(context, RunResult(True, True, [], [], [], True, ""), 0)
    proof = json.loads((tmp_path / "fault-proof.json").read_text())
    assert proof["physically_verified"] is True
    assert proof["missed_state"]["returned_versions"] == ["3"]
