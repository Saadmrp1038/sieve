"""Plan contracts exercised by both Python and the Go coordinator."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.plans import (
    FaultPlan,
    load_plan,
    parse_plan,
    validate_expression,
    write_plan,
)
from sieve_tool.runtime import runner

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "fixtures/plans/invalid-cases.json").read_text())


@pytest.mark.parametrize(
    "path", sorted((ROOT / "fixtures/reproductions").glob("*.yaml"))
)
def test_historical_plans_round_trip_without_changing_fields(path, tmp_path):
    plan = load_plan(path)
    assert plan.to_dict() == yaml.safe_load(path.read_text())
    output = tmp_path / "plan.yaml"
    write_plan(plan, output)
    assert load_plan(output) == plan


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_shared_invalid_plans_are_rejected(case):
    value = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    parent = value
    for part in case["path"][:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    key = case["path"][-1]
    if case["delete"]:
        del parent[key]
    else:
        parent[key] = case["value"]
    with pytest.raises(ValueError):
        parse_plan(value)


@pytest.mark.parametrize("expression", ["a", "a|b&c", "(a|b)&c", "a;b", "a&(b;c)"])
def test_expression_validation_keeps_valid_operator_syntax(expression):
    validate_expression(expression, {"a", "b", "c"})


def test_update_differences_must_be_paired_json_objects():
    plan = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    condition = plan["actions"][0]["trigger"]["definitions"][0]["condition"]
    condition["conditionType"] = "onObjectUpdate"
    condition["prevStateDiff"] = "{}"
    with pytest.raises(ValueError, match="together"):
        FaultPlan.model_validate(plan)
    condition["curStateDiff"] = "[]"
    with pytest.raises(ValueError, match="JSON object"):
        FaultPlan.model_validate(plan)
    condition["curStateDiff"] = '{"spec":{"replicas":2}}'
    assert FaultPlan.model_validate(plan).to_dict() == plan


def test_duplicate_trigger_names_are_rejected():
    plan = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    definitions = plan["actions"][0]["trigger"]["definitions"]
    definitions.append(copy.deepcopy(definitions[0]))
    with pytest.raises(ValueError, match="unique"):
        FaultPlan.model_validate(plan)


def test_batch_validates_every_plan_before_starting_any_cluster(tmp_path):
    (tmp_path / "first.yaml").write_text(
        (ROOT / "fixtures/plans/restart.yaml").read_text()
    )
    (tmp_path / "second.yaml").write_text("workload: example\nactions: [{}]\n")
    with patch.object(runner, "run") as execute:
        with pytest.raises(ValueError, match="second.yaml"):
            runner.run_batch(
                "controller",
                None,
                "output",
                "test",
                str(tmp_path),
                "registry",
                False,
                False,
            )
    execute.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"typo": True},
        {"test_command": "python workload.py"},
        {"test_setting": {"example": {"num_workers": -1}}},
        {"test_setting": {"example": {"process_timeouts": {"typo": 1}}}},
        {"custom_resource_definitions": [1]},
    ],
)
def test_bad_profiles_fail_at_the_loading_boundary(tmp_path, change):
    value = json.loads(
        (ROOT / "controllers/kubernetes-secret-generator/config.json").read_text()
    )
    value.update(change)
    (tmp_path / "config.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Invalid controller profile"):
        load_controller_config(tmp_path)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "60"])
def test_invalid_workload_limits_fail_before_execution(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workload_command_wait_timeout": value}))
    with pytest.raises(ValueError):
        get_common_config(path)


@pytest.mark.parametrize(
    "source",
    [
        "workload: first\nworkload: second\nactions: []\n",
        "workload: first\nactions: []\n---\nworkload: second\nactions: []\n",
    ],
)
def test_ambiguous_yaml_is_rejected_before_model_validation(tmp_path, source):
    path = tmp_path / "plan.yaml"
    path.write_text(source)
    with pytest.raises(ValueError, match="Invalid fault plan"):
        load_plan(path)


def test_wire_format_uses_existing_camel_case_field_names(tmp_path):
    value = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    action = value["actions"][0]
    action["action_type"] = action.pop("actionType")
    source = tmp_path / "plan.yaml"
    source.write_text(yaml.safe_dump(value))
    with pytest.raises(ValueError):
        load_plan(source)


def test_supported_client_endpoint_override_is_preserved():
    value = yaml.safe_load((ROOT / "fixtures/plans/restart.yaml").read_text())
    value["sieveServerAddr"] = "127.0.0.1:12345"
    assert parse_plan(value).to_dict() == value
