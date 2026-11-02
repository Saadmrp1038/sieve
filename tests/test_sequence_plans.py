"""Generated restart plans identify the first generated update, not setup."""

import json

import pytest

from sieve_tool.plans import RestartControllerAction, StateMatchCondition


@pytest.mark.parametrize(
    "operator,parent_key",
    [
        ("external-secrets", "externalsecret/default/sieve-external-secret"),
        ("cert-manager", "certificate/default/sieve-certificate"),
    ],
)
@pytest.mark.parametrize("selected", [("b", "c", "a"), ("c", "a")])
def test_restart_follows_receipt_of_the_first_generated_update(
    operator, parent_key, selected
):
    from sieve_tool.testing.sequences import sequence_plans

    plans = sequence_plans(
        controller_label=operator, parent_key=parent_key, selected=selected
    )
    assert set(plans) == {"sequence-no-fault.yaml", "sequence-restart.yaml"}
    assert plans["sequence-no-fault.yaml"].actions == []
    restart = plans["sequence-restart.yaml"]
    assert restart.workload == "bounded-sequences"
    assert len(restart.actions) == 1
    action = restart.actions[0]
    assert isinstance(action, RestartControllerAction)
    assert action.controller_label == operator
    definition = action.trigger.definitions[0]
    assert isinstance(definition.condition, StateMatchCondition)
    assert definition.condition.resource_key == parent_key
    assert json.loads(definition.condition.state_match) == {
        "metadata": {"labels": {"sieve.testing/state": "sequence-1-" + selected[0]}}
    }
    assert definition.observation_point.when == "afterControllerRecv"
    assert definition.observation_point.by == "informer"

    assert (
        action.async_ is True
    )  # replacement informer notifications must remain serviceable
