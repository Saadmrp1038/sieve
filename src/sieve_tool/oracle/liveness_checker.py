"""State projection, masks and comparisons with explicit in-memory inputs."""

import copy
from collections.abc import Collection, Iterable
from pathlib import PurePath

from deepdiff import DeepDiff

from sieve_tool.common.common import (
    METADATA_FIELDS,
    SIEVE_LEARN_VALUE_MASK,
    match_mask_regex,
)
from sieve_tool.events import (
    APIEvent,
    APIEventTypes,
    Mask,
    get_mask_by_resource_key,
    parse_key,
)
from sieve_tool.oracle.artifacts import State
from sieve_tool.oracle.checker_common import generate_alarm, kind_native_objects
from sieve_tool.oracle.policy import ComparisonPolicy


def generate_state(events: Iterable[APIEvent]) -> State:
    end_state: State = {}
    deleted = set()
    for api_event in reversed(list(events)):
        if api_event.key in deleted or api_event.key in end_state:
            continue
        if api_event.etype == APIEventTypes.DELETED:
            deleted.add(api_event.key)
        else:
            assert api_event.obj_map is not None
            end_state[api_event.key] = api_event.obj_map
    return end_state


def generate_state_mask_helper(
    mask_list: list[list[str]], obj: object, path: list[str]
) -> None:
    if type(obj) is str:
        if obj == SIEVE_LEARN_VALUE_MASK:
            already_in = False
            for mask in mask_list:
                if mask == path:
                    already_in = True
            if not already_in:
                mask_list.append(path)
            return
    if type(obj) is list:
        for i in range(len(obj)):
            val = obj[i]
            newpath = copy.deepcopy(path)
            newpath.append("*")
            generate_state_mask_helper(mask_list, val, newpath)
    elif type(obj) is dict:
        for key in obj:
            val = obj[key]
            newpath = copy.deepcopy(path)
            newpath.append(key)
            generate_state_mask_helper(mask_list, val, newpath)


def generate_state_mask(data: State) -> Mask:
    mask_map = {}
    for key in data:
        mask_list: list[list[str]] = []
        if data[key] != SIEVE_LEARN_VALUE_MASK:
            generate_state_mask_helper(mask_list, data[key], [])
            # make the masked field_path compabitable with controller shape
            metadata_field_set = set(METADATA_FIELDS)
            translated_mask_list = []
            for masked_field_path in mask_list:
                new_masked_field_path = copy.deepcopy(masked_field_path)
                if masked_field_path[0] in metadata_field_set:
                    new_masked_field_path = ["metadata"] + new_masked_field_path
                else:
                    for i in range(len(new_masked_field_path)):
                        new_masked_field_path[i] = (
                            new_masked_field_path[i][:1].lower()
                            + new_masked_field_path[i][1:]
                        )
                translated_mask_list.append(new_masked_field_path)
            translated_mask_list.sort()
            mask_map[key] = translated_mask_list
    return mask_map


def equal_path(template: str, value: str) -> bool:
    template_parts = template.split("/")
    value_parts = value.split("/")

    if len(template_parts) > len(value_parts):
        return False

    for i in range(len(template_parts)):
        if template_parts[i] == "*":
            continue
        if template_parts[i] != value_parts[i]:
            return False
    return True


def translate_apiserver_shape_to_controller_shape(
    path: list[str | int],
) -> list[str | int]:
    metadata_fields = set(METADATA_FIELDS)
    translated_path = copy.deepcopy(path)
    if len(path) > 1:
        if path[1] in metadata_fields:
            translated_path = translated_path[:1] + ["metadata"] + translated_path[1:]
        else:
            for i in range(1, len(path)):
                field = translated_path[i]
                if isinstance(field, str):
                    if field.startswith("SIEVE"):
                        break
                    translated_path[i] = field[:1].lower() + field[1:]
    return translated_path


def resource_key_should_be_masked(
    policy: ComparisonPolicy,
    resource_key: str,
    controller_family: Collection[str],
) -> bool:
    resource_type, namespace, name = parse_key(resource_key)
    if name == "sieve-testing-global-config":
        return True
    else:
        if resource_key in controller_family:
            return True
        state_mask = policy.controller.end_state_checker_mask
        for test_workload in state_mask:
            if test_workload == policy.workload or test_workload == "*":
                for masked_resource_key in state_mask[test_workload]:
                    if masked_resource_key == resource_key or PurePath(
                        "/" + resource_key
                    ).match("/" + masked_resource_key):
                        if len(state_mask[test_workload][masked_resource_key]) == 0:
                            return True
    return False


def resource_type_should_be_masked_by_controller_config(
    policy: ComparisonPolicy,
    resource_type: str,
) -> bool:
    state_mask = policy.controller.end_state_checker_mask
    for test_workload in state_mask:
        if test_workload == policy.workload or test_workload == "*":
            for masked_resource_key in state_mask[test_workload]:
                if masked_resource_key == "{}/*/*".format(resource_type):
                    if len(state_mask[test_workload][masked_resource_key]) == 0:
                        return True
    return False


def resource_field_path_should_be_masked(
    policy: ComparisonPolicy, resource_key: str, field_path_list: list[str]
) -> bool:
    # TODO: we should also mask fields as specified in the common_config
    state_mask = policy.controller.end_state_checker_mask
    for test_workload in state_mask:
        if test_workload == policy.workload or test_workload == "*":
            for masked_resource_key in state_mask[test_workload]:
                if masked_resource_key == resource_key or PurePath(
                    "/" + resource_key
                ).match("/" + masked_resource_key):
                    for masked_path in state_mask[test_workload][masked_resource_key]:
                        masked_path_str = "/".join(masked_path)
                        field_path_list_prefix = []
                        for field in field_path_list:
                            field_path_list_prefix.append(field)
                            field_path_list_prefix_str = "/".join(
                                field_path_list_prefix
                            )
                            if (
                                masked_path_str == field_path_list_prefix_str
                                or PurePath("/" + field_path_list_prefix_str).match(
                                    "/" + masked_path_str
                                )
                            ):
                                return True
    return False


def compare_states(
    reference_state: State,
    testing_state: State,
    policy: ComparisonPolicy,
    reference_family: Collection[str],
    current_family: Collection[str],
) -> list[str]:
    controller_family = set(reference_family) | set(current_family)

    messages = []
    resource_existence_messages = []
    fields_diff_messages = []
    fields_existence_messages = []

    testing_resource_to_object_map: dict[str, list[str]] = {}
    reference_resource_to_object_map: dict[str, list[str]] = {}
    resource_type_with_random_names = set()
    resource_type_masked_by_controller_config = set()

    keys_in_testing_state = set(testing_state.keys())
    keys_in_reference_state = set(reference_state.keys())

    for resource_key in keys_in_testing_state.union(keys_in_reference_state):
        if kind_native_objects(resource_key):
            continue
        if resource_key_should_be_masked(policy, resource_key, controller_family):
            continue
        resource_type, namespace, name = parse_key(resource_key)
        if resource_type_should_be_masked_by_controller_config(policy, resource_type):
            resource_type_masked_by_controller_config.add(resource_type)
        if resource_key in testing_state:
            if resource_type not in testing_resource_to_object_map:
                testing_resource_to_object_map[resource_type] = []
            testing_resource_to_object_map[resource_type].append(resource_key)
        if resource_key in reference_state:
            if resource_type not in reference_resource_to_object_map:
                reference_resource_to_object_map[resource_type] = []
            reference_resource_to_object_map[resource_type].append(resource_key)
            if reference_state[resource_key] == SIEVE_LEARN_VALUE_MASK:
                resource_type_with_random_names.add(resource_type)

    keys_in_testing_state_only = keys_in_testing_state.difference(
        keys_in_reference_state
    )
    keys_in_reference_state_only = keys_in_reference_state.difference(
        keys_in_testing_state
    )

    for resource_key in keys_in_testing_state_only:
        if kind_native_objects(resource_key):
            continue
        if resource_key_should_be_masked(policy, resource_key, controller_family):
            continue
        resource_type, namespace, name = parse_key(resource_key)
        if resource_type not in resource_type_with_random_names:
            resource_existence_messages.append(
                generate_alarm(
                    "End state inconsistency - more objects than reference:",
                    "{} {}".format(
                        "/".join([resource_type, namespace, name]),
                        "is not seen after reference run, but seen after testing run",
                    ),
                )
            )
    for resource_key in keys_in_reference_state_only:
        if kind_native_objects(resource_key):
            continue
        if resource_key_should_be_masked(policy, resource_key, controller_family):
            continue
        resource_type, namespace, name = parse_key(resource_key)
        if resource_type not in resource_type_with_random_names:
            resource_existence_messages.append(
                generate_alarm(
                    "End state inconsistency - fewer objects than reference:",
                    "{} {}".format(
                        "/".join([resource_type, namespace, name]),
                        "is seen after reference run, but not seen after testing run",
                    ),
                )
            )
    for resource_type in resource_type_with_random_names:
        if resource_type in resource_type_masked_by_controller_config:
            continue
        if resource_type not in testing_resource_to_object_map:
            testing_resource_to_object_map[resource_type] = []
        if resource_type not in reference_resource_to_object_map:
            reference_resource_to_object_map[resource_type] = []
        delta = len(testing_resource_to_object_map[resource_type]) - len(
            reference_resource_to_object_map[resource_type]
        )
        if delta != 0:
            testing_list = testing_resource_to_object_map[resource_type]
            reference_list = reference_resource_to_object_map[resource_type]
            testing_list.sort()
            reference_list.sort()
            resource_existence_messages.append(
                generate_alarm(
                    "End state inconsistency - more objects than reference:"
                    if delta > 0
                    else "End state inconsistency - fewer objects than reference:",
                    "{} {} {} {} {} {} {} {} {}".format(
                        len(reference_list),
                        resource_type + " object(s)",
                        "seen after reference run",
                        reference_list,
                        "but",
                        len(testing_list),
                        resource_type + " object(s)",
                        "seen after testing run",
                        testing_list,
                    ),
                )
            )

    tdiff = DeepDiff(
        reference_state,
        testing_state,
        ignore_order=False,
        view="tree",
        threshold_to_diff_deeper=0,
        zip_ordered_iterables=True,
    )

    for delta_type in tdiff:
        for key in tdiff[delta_type]:
            untranslated_path = key.path(output_format="list")
            resource_key = untranslated_path[0]
            if kind_native_objects(resource_key):
                continue
            resource_type, namespace, name = parse_key(resource_key)
            path = untranslated_path
            if resource_type not in policy.controller.custom_resource_definitions:
                path = translate_apiserver_shape_to_controller_shape(untranslated_path)
            mask_keys = set(
                get_mask_by_resource_key(
                    policy.common.field_key_mask,
                    resource_key,
                )
            )
            mask_paths = set(
                get_mask_by_resource_key(
                    policy.common.field_path_mask,
                    resource_key,
                )
            )

            if len(path) == 1:
                continue

            if delta_type in ["values_changed", "type_changes"]:
                if (
                    key.t1 == SIEVE_LEARN_VALUE_MASK
                    or match_mask_regex(key.t1)
                    or match_mask_regex(key.t2)
                ):
                    continue

            should_be_masked = False
            # Search for boring keys
            for kp in path:
                if kp in mask_keys:
                    should_be_masked = True
                    break
            # Search for boring paths
            if len(path) > 2:
                for rule in mask_paths:
                    if equal_path(rule, "/".join([str(x) for x in path[1:]])):
                        should_be_masked = True
                        break
            if should_be_masked:
                continue

            field_path_for_print = ""
            field_path_list = []
            for field in map(str, path[1:]):
                field_path_list.append(str(field))
                if field.isdigit():
                    field_path_for_print += "[{}]".format(field)
                else:
                    field_path_for_print += '["{}"]'.format(field)

            if resource_key_should_be_masked(
                policy,
                resource_key,
                controller_family,
            ):
                continue

            if resource_field_path_should_be_masked(
                policy, resource_key, field_path_list
            ):
                continue

            if delta_type in ["dictionary_item_added", "iterable_item_added"]:
                fields_existence_messages.append(
                    generate_alarm(
                        "End state inconsistency - more object fields than reference:",
                        "{}{} {} {} {}".format(
                            resource_key,
                            field_path_for_print,
                            "not seen after reference run, but is",
                            key.t2,
                            "after testing run",
                        ),
                    )
                )
            elif delta_type in ["dictionary_item_removed", "iterable_item_removed"]:
                learned_value = key.t1
                if learned_value == SIEVE_LEARN_VALUE_MASK:
                    learned_value = "a nondeterministic value"
                fields_existence_messages.append(
                    generate_alarm(
                        "End state inconsistency - fewer object fields than reference:",
                        "{}{} {} {} {}".format(
                            resource_key,
                            field_path_for_print,
                            "is",
                            learned_value,
                            "after reference run, but not seen after testing run",
                        ),
                    )
                )
            elif delta_type == "values_changed" or delta_type == "type_changes":
                fields_diff_messages.append(
                    generate_alarm(
                        "End state inconsistency - object field has a different value:",
                        "{}{} {} {} {} {} {}".format(
                            resource_key,
                            field_path_for_print,
                            "is",
                            key.t1,
                            "after reference run, but",
                            key.t2,
                            "after testing run",
                        ),
                    )
                )
            else:
                raise AssertionError(f"unsupported difference type {delta_type}")

    resource_existence_messages.sort()
    fields_diff_messages.sort()
    fields_existence_messages.sort()

    messages = (
        resource_existence_messages + fields_diff_messages + fields_existence_messages
    )
    return messages
