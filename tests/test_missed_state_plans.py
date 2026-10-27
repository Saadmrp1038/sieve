"""Independent updates can identify the state a cache must reach to resume."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sieve_tool.analysis.analyze import generate_test_plans_from_learn_run
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.plans import load_plan, parse_plan
from sieve_tool.trace import Marker


def test_update_target_state_is_preserved_in_a_plan():
    condition = {
        "conditionType": "onObjectUpdateTo",
        "resourceKey": "secret/default/example",
        "occurrence": 1,
        "stateMatch": json.dumps(
            {"metadata": {"labels": {"sieve.testing/state": "C"}}}
        ),
    }
    plan = parse_plan(
        {
            "workload": "independent-updates",
            "actions": [
                {
                    "actionType": "resumeController",
                    "pauseAt": "beforeControllerRead",
                    "pauseScope": "secret/default/example",
                    "trigger": {
                        "definitions": [
                            {
                                "triggerName": "later",
                                "condition": condition,
                                "observationPoint": {
                                    "when": "afterControllerRecv",
                                    "by": "metadata-informer",
                                },
                            }
                        ],
                        "expression": "later",
                    },
                }
            ],
        }
    )
    assert (
        plan.to_dict()["actions"][0]["trigger"]["definitions"][0]["condition"]
        == condition
    )


@pytest.mark.parametrize(
    "case", ["valid", "controller_producer", "missing_receipt", "unread_state"]
)
def test_learning_generates_only_independent_overwrite_schedules(tmp_path, case):
    root = Path(__file__).resolve().parents[1]
    profile = replace(
        load_controller_config(root / "controllers/external-secrets"),
        test_setting={
            "miss-child-updates": {"num_workers": 0, "independent_updates": True},
        },
    )
    consumer = profile.annotated_reconcile_functions[
        "pkg/controllers/externalsecret/externalsecret_controller.go"
    ]
    source = {
        "schema_version": 1,
        "status": "completed",
        "workload": "miss-child-updates",
        "resource_key": "secret/default/example",
        "updates": [],
    }
    lines = []
    for index, phase in enumerate("ABCD"):
        obj = {
            "kind": "Secret",
            "metadata": {
                "name": "example",
                "namespace": "default",
                "uid": "child-1",
                "resourceVersion": str(index + 1),
                "labels": {"sieve.testing/state": phase},
            },
        }
        projection = {"metadata": {"labels": {"sieve.testing/state": phase}}}
        source["updates"].append(
            {"producer": "workload", "state": projection, "response": obj}
        )
        for offset, observer in enumerate(("informer", "metadata-informer")):
            event_id = str(index * 2 + offset)
            lines.append(
                "\t".join(
                    [
                        Marker.BEFORE_HEAR,
                        event_id,
                        "Updated",
                        "secret",
                        json.dumps(obj),
                        observer,
                    ]
                )
            )
            lines.append("\t".join([Marker.AFTER_HEAR, event_id]))
        lines.append("\t".join([Marker.BEFORE_RECONCILE, consumer, str(index)]))
        lines.append(
            "\t".join(
                [
                    Marker.CACHE_READ,
                    "Get",
                    "secret",
                    "default",
                    "example",
                    consumer,
                    "NoError",
                    json.dumps(obj),
                ]
            )
        )
        lines.append("\t".join([Marker.AFTER_RECONCILE, consumer, str(index)]))
    (tmp_path / "sieve-server.log").write_text("\n".join(lines) + "\n")
    (tmp_path / "workload-updates.json").write_text(json.dumps(source))
    (tmp_path / "mask.json").write_text("{}")
    (tmp_path / "state.json").write_text(json.dumps({source["resource_key"]: obj}))
    context = SimpleNamespace(
        result_dir=str(tmp_path),
        result_root_dir=str(tmp_path),
        oracle_dir=str(tmp_path),
        controller="external-secrets",
        controller_config=profile,
        common_config=get_common_config(),
        test_workload="miss-child-updates",
    )
    if case == "controller_producer":
        source["updates"][2]["producer"] = "controller"
        (tmp_path / "workload-updates.json").write_text(json.dumps(source))
        with pytest.raises(ValueError):
            generate_test_plans_from_learn_run(context)
        assert not list((tmp_path / "unobserved-state").glob("*.yaml"))
        return
    if case == "missing_receipt":
        lines = [
            line
            for line in lines
            if not (
                line.split("\t")[0] in (Marker.BEFORE_HEAR, Marker.AFTER_HEAR)
                and line.split("\t")[1] == "4"
            )
        ]
        (tmp_path / "sieve-server.log").write_text("\n".join(lines) + "\n")
    if case == "unread_state":
        lines = [
            line
            for line in lines
            if not (
                line.startswith(Marker.CACHE_READ)
                and '"sieve.testing/state": "B"' in line
            )
        ]
        (tmp_path / "sieve-server.log").write_text("\n".join(lines) + "\n")
    generate_test_plans_from_learn_run(context)
    paths = sorted((tmp_path / "unobserved-state").glob("*.yaml"))
    expected_phases = (
        "" if case == "missing_receipt" else "C" if case == "unread_state" else "BC"
    )
    assert len(paths) == len(expected_phases)
    for path, phase in zip(paths, expected_phases, strict=True):
        later = {"B": "C", "C": "D"}[phase]
        plan = load_plan(path)
        pause, resume = plan.actions
        assert pause.pause_by == consumer
        start = pause.trigger.definitions[0]
        assert start.observation_point.when == "beforeAPIServerRecv"
        assert (
            json.loads(start.condition.state_match)["metadata"]["labels"][
                "sieve.testing/state"
            ]
            == phase
        )
        assert {d.observation_point.by for d in resume.trigger.definitions} == {
            "informer",
            "metadata-informer",
        }
        assert all(
            json.loads(d.condition.state_match)["metadata"]["labels"][
                "sieve.testing/state"
            ]
            == later
            for d in resume.trigger.definitions
        )
        assert "&" in resume.trigger.expression
