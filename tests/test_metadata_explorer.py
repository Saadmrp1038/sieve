"""Bounded case generation and frozen matched plans through public interfaces."""


def test_explorer_covers_return_histories_and_both_adjacent_placements_for_both_operators():
    from sieve_tool.testing.metadata_cases import explore

    cases = explore(max_cases=16)
    assert len(cases) == len({c.case_id for c in cases}) == 16
    assert {c.operator for c in cases} == {"external-secrets", "cert-manager"}
    for operator in ("external-secrets", "cert-manager"):
        for channel in ("labels", "annotations"):
            for history, expected in (
                ("remove-readd", [("add", "one"), ("remove", None), ("add", "two")]),
                (
                    "restore-value",
                    [("add", "one"), ("change", "two"), ("change", "one")],
                ),
            ):
                selected = [
                    c
                    for c in cases
                    if (c.operator, c.channel, c.history_name)
                    == (operator, channel, history)
                ]
                assert [
                    (c.hold_after_step, c.release_after_step) for c in selected
                ] == [(2, 3), (1, 2)]
                for case in selected:
                    assert [
                        (s.operation, s.value) for s in case.history.steps
                    ] == expected
    assert [c.operator for c in explore(max_cases=2)] == [
        "external-secrets",
        "cert-manager",
    ]


import pytest


@pytest.mark.parametrize("limit", [0, 17, True, 1.5])
def test_explorer_rejects_ambiguous_or_excessive_bounds(limit):
    from sieve_tool.testing.metadata_cases import explore

    with pytest.raises(ValueError, match="1–16"):
        explore(max_cases=limit)


@pytest.mark.parametrize(
    "operators", [(), ("unknown",), ("cert-manager", "cert-manager")]
)
def test_explorer_requires_a_nonempty_unique_set_of_supported_operators(operators):
    from sieve_tool.testing.metadata_cases import explore

    with pytest.raises(ValueError):
        explore(operators=operators)


def test_each_case_has_scoped_matched_plans_and_freezes_its_history_and_placement():
    from sieve_tool.common.config import load_controller_config
    from sieve_tool.plans import parse_plan
    from sieve_tool.testing.metadata_cases import explore

    for case in explore():
        control, fault = case.plan("observe").to_dict(), case.plan("hold").to_dict()
        assert parse_plan(control).to_dict() == control
        assert parse_plan(fault).to_dict() == fault
        assert control["actions"] == fault["actions"] == []
        assert control["workload"] == fault["workload"] == case.workload
        schedule = fault["deliveryOverlap"]
        assert schedule["resourceKey"] in fault["coverageResources"]
        assert (
            schedule["reconciler"]
            in load_controller_config(
                "controllers/" + case.operator
            ).annotated_reconcile_functions.values()
        )
        assert (
            schedule["holdPhase"]
            == f"metadata-{case.hold_after_step}-{case.history.steps[case.hold_after_step - 1].operation}"
        )
        assert (
            schedule["releasePhase"]
            == f"metadata-{case.release_after_step}-{case.history.steps[case.release_after_step - 1].operation}"
        )
        assert schedule["timeoutSeconds"] == 20
        control["deliveryOverlap"]["mode"] = "hold"
        assert control == fault
        plan = case.metadata_plan()
        assert plan["schema_version"] == 2 and plan["workers"] == 10
        assert plan["case_id"] == case.case_id
        assert plan["placement"] == {
            "hold_after_step": case.hold_after_step,
            "release_after_step": case.release_after_step,
        }


def test_planner_cli_freezes_a_bounded_deterministic_campaign_without_executing(
    tmp_path,
):
    import json
    import os
    import subprocess
    import sys

    outputs = [tmp_path / "first", tmp_path / "second"]
    for output in outputs:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "sieve_tool.testing.metadata_explorer",
                "plan",
                "--max-cases",
                "3",
                "--output",
                str(output),
            ],
            env={"PATH": os.defpath, "PYTHONPATH": "src"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        manifest = json.loads((output / "exploration.json").read_text())
        assert manifest["selected_cases"] == 3 and manifest["generated_cases"] == 16
        assert manifest["executed_workloads"] == manifest["fault_attempts"] == 0
        assert manifest["workers"] == 10 and manifest["automatic_retries"] == 0
        assert (
            len(list(output.rglob("control.yaml")))
            == len(list(output.rglob("fault.yaml")))
            == 3
        )
    contents = lambda root: {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    assert contents(outputs[0]) == contents(outputs[1])
    before = contents(outputs[0])
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sieve_tool.testing.metadata_explorer",
            "plan",
            "--output",
            str(outputs[0]),
        ],
        capture_output=True,
    )
    assert result.returncode == 2 and contents(outputs[0]) == before


def test_named_cases_have_existing_operator_runtime_settings():
    from sieve_tool.common.config import load_controller_config
    from sieve_tool.testing.metadata_cases import explore

    for case in explore():
        settings = load_controller_config("controllers/" + case.operator).test_setting[
            case.workload
        ]
        assert settings["num_workers"] == 0  # Kind worker nodes, not reconcile workers.
        assert settings["write_trigger"] == "occurrence"


def test_named_plan_rejects_a_different_declared_overlap_edge():
    from sieve_tool.plans import parse_plan
    from sieve_tool.testing.metadata_cases import explore

    case = explore(max_cases=1)[0]
    value = case.plan("hold").to_dict()
    value["deliveryOverlap"].update(
        holdPhase="metadata-1-add", releasePhase="metadata-2-remove"
    )
    with pytest.raises(ValueError, match="placement"):
        parse_plan(value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operator", "unknown"),
        ("channel", "other"),
        ("history_name", "unknown"),
        ("hold_after_step", True),
        ("hold_after_step", 0),
        ("hold_after_step", 3),
    ],
)
def test_public_case_cannot_escape_supported_bounds(field, value):
    from sieve_tool.testing.metadata_cases import MetadataCase

    values = dict(
        operator="external-secrets",
        channel="labels",
        history_name="remove-readd",
        hold_after_step=2,
    )
    values[field] = value
    with pytest.raises(ValueError):
        MetadataCase(**values)
