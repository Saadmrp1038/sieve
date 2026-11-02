"""No-fault controls use the same saved-evidence comparisons as fault runs."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("added_count,exit_code", [(1, 0), (2, 1)])
def test_empty_action_control_is_compared_without_a_harness_error(
    tmp_path, added_count, exit_code
):
    output = tmp_path / "results"
    case = output / "cloudnative-pg/ca-secret-bootstrap/test/no-fault"
    reference = tmp_path / "oracles/cloudnative-pg/ca-secret-bootstrap"
    case.mkdir(parents=True)
    reference.mkdir(parents=True)
    plan = tmp_path / "no-fault.yaml"
    plan.write_text("workload: ca-secret-bootstrap\nactions: []\n")
    (case / plan.name).write_bytes(plan.read_bytes())
    artifacts = {
        "state.json": {"secret/default/application": {"data": {"value": "ok"}}},
        "event.json": {"secret/default/application": {"ADDED": 1, "DELETED": 0}},
        "controller_family.json": [],
    }
    for name, value in artifacts.items():
        for directory in (case, reference):
            (directory / name).write_text(json.dumps(value))
    (case / "event.json").write_text(
        json.dumps({"secret/default/application": {"ADDED": added_count, "DELETED": 0}})
    )
    (case / "sieve-server.log").write_text(
        "Sieve test coordinator finishes all actions\n"
    )
    (case / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (case / "streamed-controller.log").write_text("")
    (case / "process-outcome.json").write_text('{"status":"completed","returncode":0}')
    config = tmp_path / "settings.json"
    config.write_text('{"generate_debugging_information_enabled":false}')
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sieve_tool",
            "--repo",
            str(ROOT),
            "--config",
            str(config),
            "test",
            "-c",
            "cloudnative-pg",
            "-p",
            str(plan),
            "--output",
            str(output),
            "--oracle-root",
            str(reference.parents[1]),
            "--postprocess",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    raw = json.loads(
        (output / "cloudnative-pg-ca-secret-bootstrap-no-fault.yaml.json").read_text()
    )
    verdict = next(iter(raw["cloudnative-pg"]["ca-secret-bootstrap"]["test"].values()))
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert verdict["no_exception"] and verdict["workload_completed"]
    if added_count == 1:
        assert verdict["number_errors"] == 0
    else:
        assert verdict["detected_errors"] == [
            "State-update summaries inconsistency: secret/default/application "
            "ADDED inconsistency: 1 event(s) seen during reference run, "
            "but 2 seen during testing run"
        ]
