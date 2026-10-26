import json
from dataclasses import dataclass

from sieve_tool.oracle.safety_checker import CustomState, SafetyChecker


@dataclass(frozen=True)
class SafetyCheckerSuite:
    resource_keys: list[str]
    checker_name: str
    checker_function: SafetyChecker


# Customized safety checker example:
# This checker checks a safety property:
# The rabbitmq-cluster-server statefulset should never have more than one replica
# (the property is not correct; this is just an example)
# Sieve will apply this checker to every state
# As long as there is one state where the property does not hold (return false in the function)
# Sieve will report an alarm
def example_rabbitmq_safety_checker(state: CustomState) -> bool:
    key = "statefulset/default/rabbitmq-cluster-server"
    if key in state:
        value = state[key]
        assert value is not None
        object_state = json.loads(value)
        if object_state["Spec"]["Replicas"] > 1:
            return False
    return True


# This is another example that is similar to the checker mentioned in
# https://github.com/sieve-project/sieve/issues/42
def example_foo_safety_checker(state: CustomState) -> bool:
    key1 = "statefulset/default/sts1"
    key2 = "statefulset/default/sts2"
    ratio = 0.7
    if key1 in state and key2 in state:
        first, second = state[key1], state[key2]
        assert first is not None and second is not None
        object_state1 = json.loads(first)
        object_state2 = json.loads(second)
        if (
            object_state1["Spec"]["Replicas"] * ratio
            < object_state2["Spec"]["Replicas"]
        ):
            return False
    return True


# Users can specify customized safety checker by adding SafetyCheckerSuite here
# Each SafetyCheckerSuite has three elements:
# 1. the list of resources to check, here there is only one resource to check: statefulset/default/rabbitmq-cluster-server
# 2. the checker's name
# 3. the checker function
customized_safety_checker_suites: list[SafetyCheckerSuite] = [
    # SafetyCheckerSuite(
    #     ["statefulset/default/rabbitmq-cluster-server"],
    #     "example_checker",
    #     example_rabbitmq_safety_checker,
    # )
]
