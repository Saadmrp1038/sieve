"""History summaries and custom safety checks on in-memory evidence."""

import copy
from collections.abc import Callable, Collection, Iterable
from pathlib import PurePath

from sieve_tool.common.common import SIEVE_LEARN_VALUE_MASK
from sieve_tool.events import (
    APIEvent,
    APIEventTypes,
    extract_generate_name,
    is_generated_random_name,
)
from sieve_tool.oracle.artifacts import Digest, History
from sieve_tool.oracle.checker_common import generate_alarm
from sieve_tool.oracle.policy import ComparisonPolicy

type CustomState = dict[str, str | None]
type SafetyChecker = Callable[[CustomState], bool]

api_event_empty_entry = {APIEventTypes.ADDED: 0, APIEventTypes.DELETED: 0}


def masked_resource_key_for_state_update_summary_checker(
    resource_key: str, policy: ComparisonPolicy
) -> bool:
    test_workload = policy.workload
    controller_mask = policy.controller.state_update_summary_checker_mask
    common_mask = policy.common.state_update_summary_checker_mask
    for masked_test_workload in controller_mask:
        if masked_test_workload == "*" or masked_test_workload == test_workload:
            for masked_key in controller_mask[masked_test_workload]:
                if masked_key == resource_key or PurePath("/" + resource_key).match(
                    "/" + masked_key
                ):
                    return True
    for masked_key in common_mask:
        if masked_key == resource_key or PurePath("/" + resource_key).match(
            "/" + masked_key
        ):
            return True
    return False


def generate_history(events: Iterable[APIEvent]) -> History:
    history: History = []
    for api_event in events:
        history.append(
            {
                "number": len(history),
                "etype": api_event.etype,
                "key": api_event.key,
                "state": api_event.obj_str,
            }
        )
    return history


def generate_history_digest(
    events: Iterable[APIEvent], selected_types: Collection[str]
) -> dict[str, dict[str, int]]:
    state_update_summary: dict[str, dict[str, int]] = {}
    for api_event in events:
        key = api_event.key
        if api_event.etype not in selected_types:
            continue
        generate_name = extract_generate_name(api_event.obj_map)
        if generate_name is not None:
            if is_generated_random_name(api_event.name, generate_name):
                key = key[:-5] + "*"
        if key not in state_update_summary:
            state_update_summary[key] = copy.deepcopy(api_event_empty_entry)
        state_update_summary[key][api_event.etype] += 1
    return state_update_summary


def apply_safety_checker(
    history: History,
    resource_keys: Collection[str],
    checker_name: str,
    customized_checker: SafetyChecker,
) -> list[str]:
    messages = []
    current_state: CustomState = {}
    for key in resource_keys:
        current_state[key] = None
    for event in history:
        resource_key = event["key"]
        if resource_key in resource_keys:
            if event["etype"] == "DELETED":
                current_state[event["key"]] = None
            else:
                current_state[event["key"]] = event["state"]
            existing_resource_cnt = 0
            for key in current_state:
                if current_state[key] is not None:
                    existing_resource_cnt += 1
            if existing_resource_cnt == len(current_state):
                if not customized_checker(current_state):
                    messages.append(
                        generate_alarm(
                            "Safety violation:",
                            "checker {} failed on state {} in history.json".format(
                                checker_name, event["number"]
                            ),
                        )
                    )
    messages.sort()
    return messages


def compare_history_digests(
    canonicalized_events: Digest,
    testing_events: Digest,
    controller_family: Collection[str],
    policy: ComparisonPolicy,
) -> list[str]:
    messages = []

    # checking events inconsistency for each key
    testing_keys = set(testing_events.keys())
    learning_keys = set(canonicalized_events.keys())
    for key in testing_keys.intersection(learning_keys):
        if canonicalized_events[key] == SIEVE_LEARN_VALUE_MASK:
            continue
        if key in controller_family:
            continue
        if masked_resource_key_for_state_update_summary_checker(key, policy):
            continue
        reference_counts = canonicalized_events[key]
        assert isinstance(reference_counts, dict)
        counts = testing_events[key]
        if isinstance(counts, str):
            continue
        for etype in counts:
            if etype not in policy.common.state_update_summary_check_event_list:
                continue
            if reference_counts[etype] == SIEVE_LEARN_VALUE_MASK:
                continue
            if counts[etype] != reference_counts[etype]:
                messages.append(
                    generate_alarm(
                        "State-update summaries inconsistency:",
                        "{} {} inconsistency: {} event(s) seen during reference run, but {} seen during testing run".format(
                            key,
                            etype,
                            str(reference_counts[etype]),
                            str(counts[etype]),
                        ),
                    )
                )
    messages.sort()
    return messages
