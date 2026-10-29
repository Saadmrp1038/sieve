"""Baseline diagnostics expose unsupported occurrences without amending YAML."""

import json
from pathlib import Path

import pytest
import yaml

from sieve_tool import cli

ROOT = Path(__file__).resolve().parents[1]


def test_stale_coverage_counts_plans_instead_of_reconnect_actions(tmp_path, capsys):
    plans = tmp_path / "plans"
    plans.mkdir()
    document = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    action = document["actions"][0]
    action.update(
        actionType="reconnectController", reconnectAPIServer="kind-control-plane2"
    )
    document["actions"].append(dict(action))
    (plans / "two-reconnects.yaml").write_text(yaml.safe_dump(document))
    trace = tmp_path / "baseline.log"
    trace.write_text("")
    assert (
        cli.main(
            [
                "--repo",
                str(ROOT),
                "check-plans",
                "--plans",
                str(plans),
                "--trace",
                str(trace),
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["generated"] == report["stale_state_plans"] == 1


def test_plan_check_reports_an_unobserved_occurrence_and_zero_stale_coverage(
    tmp_path, capsys
):
    plans = tmp_path / "plans"
    plans.mkdir()
    document = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    trigger = document["actions"][0]["trigger"]["definitions"][0]
    trigger["condition"]["occurrence"] = 2
    trigger["observationPoint"]["by"] = "operator.Reconcile"
    selected = plans / "restart.yaml"
    selected.write_text(yaml.safe_dump(document))
    original = selected.read_bytes()
    trace = tmp_path / "baseline.log"
    trace.write_text(
        "[SIEVE-AFTER-REST-WRITE]\t1\tCreate\toperator.Reconcile\tNoError\tsecret\tdefault\tsieve-generated-secret\t{}\n"
    )
    assert (
        cli.main(
            [
                "--repo",
                str(ROOT),
                "check-plans",
                "--plans",
                str(plans),
                "--trace",
                str(trace),
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    assert report["generated"] == 1
    assert report["stale_state_plans"] == 0
    assert report["plans"][0]["diagnostics"][0]["code"] == "occurrence_not_observed"
    assert selected.read_bytes() == original


@pytest.mark.parametrize("independent", [False, True])
def test_plan_check_warns_when_resume_depends_on_the_paused_consumer(
    tmp_path, capsys, independent
):
    plans = tmp_path / "plans"
    plans.mkdir()
    document = yaml.safe_load((ROOT / "fixtures/plans/missed-state.yaml").read_text())
    document["actions"][0]["pauseBy"] = "operator.Reconcile"
    (plans / "pause.yaml").write_text(yaml.safe_dump(document))
    trace = tmp_path / "baseline.log"
    trace.write_text(
        "[SIEVE-AFTER-REST-WRITE]\t1\tCreate\toperator.Reconcile\tNoError\tsecret\tdefault\tsieve-generated-secret\t{}\n"
    )
    if independent:
        (tmp_path / "workload-updates.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "completed",
                    "workload": document["workload"],
                    "resource_key": "secret/default/sieve-generated-secret",
                    "updates": [
                        {
                            "producer": "workload",
                            "state": {"data": {"phase": phase}},
                            "response": {
                                "kind": "Secret",
                                "metadata": {
                                    "name": "sieve-generated-secret",
                                    "namespace": "default",
                                    "uid": "one-child",
                                    "resourceVersion": str(i),
                                },
                                "data": {"phase": phase},
                            },
                        }
                        for i, phase in enumerate("ABC", 1)
                    ],
                }
            )
        )
    assert (
        cli.main(
            [
                "--repo",
                str(ROOT),
                "check-plans",
                "--plans",
                str(plans),
                "--trace",
                str(trace),
            ]
        )
        == 1
    )
    report = json.loads(capsys.readouterr().out)
    codes = {d["code"] for d in report["plans"][0]["diagnostics"]}
    assert ("resume_depends_on_paused_consumer" in codes) is (not independent)
