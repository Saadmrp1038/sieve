"""Independent updates can identify the state a cache must reach to resume."""

import json

from sieve_tool.plans import parse_plan


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
