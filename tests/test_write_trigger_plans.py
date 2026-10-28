"""Replayable write predicates count operations and scope generated names explicitly."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sieve_tool.analysis.analyze import (
    build_controller_event_graph,
    generate_test_plans,
)
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.plans import load_plan, parse_plan
from sieve_tool.trace import Marker

ROOT = Path(__file__).resolve().parents[1]


def pattern_plan(key, enabled=True):
    return {
        "workload": "example",
        "actions": [
            {
                "actionType": "restartController",
                "controllerLabel": "operator",
                "trigger": {
                    "definitions": [
                        {
                            "triggerName": "write",
                            "condition": {
                                "conditionType": "onObjectCreate",
                                "resourceKey": key,
                                "resourceKeyPattern": enabled,
                                "occurrence": 1,
                            },
                            "observationPoint": {
                                "when": "afterControllerWrite",
                                "by": "example.Reconcile",
                            },
                        }
                    ],
                    "expression": "write",
                },
            }
        ],
    }


def test_name_prefix_pattern_requires_explicit_scoped_opt_in():
    key = "secret/default/sieve-certificate-*"
    assert (
        parse_plan(pattern_plan(key)).to_dict()["actions"][0]["trigger"]["definitions"][
            0
        ]["condition"]["resourceKeyPattern"]
        is True
    )
    # Existing literal keys remain literal unless explicitly enabled.
    assert parse_plan(pattern_plan(key, False))
    for invalid in (
        "secret/*/example-*",
        "*/default/example-*",
        "secret/default/*",
        "secret/default/ex?*",
        "secret/default/ex*/other",
        "secret/default/exact",
    ):
        with pytest.raises(ValueError, match="resourceKeyPattern"):
            parse_plan(pattern_plan(invalid))


@pytest.mark.parametrize(
    ("last_error", "expected_occurrences"),
    [("NoError", [1, 1, 2, 1, 2]), ("Conflict", [1, 1, 2, 1])],
)
def test_operation_planning_counts_all_successful_writes_per_consumer_and_selector(
    tmp_path, last_error, expected_occurrences
):
    profile = load_controller_config(ROOT / "controllers/cert-manager")
    consumer = next(iter(profile.annotated_reconcile_functions.values()))
    other = list(profile.annotated_reconcile_functions.values())[1]
    # Set the opt-in on a public profile object. Load validation is tested separately.
    profile.test_setting["delete-child"].update(
        write_trigger="occurrence",
        generated_name_prefixes=["secret/default/sieve-certificate-*"],
    )
    lines = []
    writes = [
        (consumer, "StatusUpdate", "certificate", "sieve-certificate", "NoError"),
        (other, "Patch", "certificate", "sieve-certificate", "NoError"),
        (consumer, "Patch", "certificate", "sieve-certificate", "NoError"),
        (consumer, "Create", "secret", "sieve-certificate-abc", "NoError"),
        (consumer, "Create", "secret", "sieve-certificate-def", last_error),
    ]
    for i, (frame, operation, kind, name, error) in enumerate(writes):
        obj = {
            "kind": kind.title(),
            "metadata": {"namespace": "default", "name": name},
            "status": {"phase": str(i)},
        }
        lines += [
            "\t".join([Marker.BEFORE_RECONCILE, frame, str(i)]),
            "\t".join(
                [
                    Marker.CACHE_READ,
                    "Get",
                    kind,
                    "default",
                    name,
                    frame,
                    "NoError",
                    json.dumps({**obj, "status": {"phase": "before"}}),
                ]
            ),
            "\t".join([Marker.BEFORE_WRITE, str(i)]),
            "\t".join(
                [
                    Marker.AFTER_WRITE,
                    str(i),
                    operation,
                    frame,
                    error,
                    kind,
                    "default",
                    name,
                    json.dumps(obj),
                ]
            ),
            "\t".join([Marker.AFTER_RECONCILE, frame, str(i)]),
        ]
    trace = tmp_path / "sieve-server.log"
    trace.write_text("\n".join(lines) + "\n")
    (tmp_path / "mask.json").write_text("{}")
    (tmp_path / "state.json").write_text("{}")
    context = SimpleNamespace(
        result_dir=str(tmp_path),
        oracle_dir=str(tmp_path),
        controller_config=profile,
        test_workload="delete-child",
        common_config=replace(
            get_common_config(),
            effective_updates_pruning_enabled=False,
            nondeterministic_pruning_enabled=False,
        ),
    )
    graph = build_controller_event_graph(context, trace, tmp_path)
    counts = generate_test_plans(context, "intermediate-state", graph)
    assert counts[-1] == len(expected_occurrences)
    plans = [
        load_plan(
            tmp_path / "intermediate-state" / f"intermediate-state-test-plan-{i}.yaml"
        )
        for i in range(1, len(expected_occurrences) + 1)
    ]
    conditions = [p.actions[0].trigger.definitions[0].condition for p in plans]
    assert [c.occurrence for c in conditions] == expected_occurrences
    assert all(
        "prevStateDiff" not in c.model_dump(by_alias=True, exclude_unset=True)
        for c in conditions
    )
    assert [c.resource_key for c in conditions[3:]] == [
        "secret/default/sieve-certificate-*"
    ] * (len(expected_occurrences) - 3)
    assert all(c.resource_key_pattern for c in conditions[3:])
