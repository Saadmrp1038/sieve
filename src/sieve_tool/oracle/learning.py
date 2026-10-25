"""Learn reference data from two in-memory baselines."""

import copy
from collections.abc import Iterable, Sequence
from typing import Any

from deepdiff import DeepDiff

from sieve_tool.common.common import SIEVE_LEARN_VALUE_MASK
from sieve_tool.events import APIEvent, Object
from sieve_tool.oracle.artifacts import Digest


def second_pass_learn_trim[T: Object](base_resources: T, twice_resources: T) -> T:
    def nested_set(dic: Any, keys: list[str | int], value: str) -> None:
        for key in keys[:-1]:
            dic = dic[key]
        dic[keys[-1]] = value

    stored_learn = copy.deepcopy(base_resources)
    ddiff = DeepDiff(
        twice_resources,
        base_resources,
        ignore_order=False,
        view="tree",
        threshold_to_diff_deeper=0,
        zip_ordered_iterables=True,
    )

    if "values_changed" in ddiff:
        for key in ddiff["values_changed"]:
            nested_set(
                stored_learn, key.path(output_format="list"), SIEVE_LEARN_VALUE_MASK
            )

    # Learn varying fields in either direction, but retain resource identities
    # from one baseline. Unioning random names from two runs doubles the
    # expected object count (e.g. one controller Pod from each run).
    for change in ("dictionary_item_added", "dictionary_item_removed"):
        for key in ddiff.get(change, []):
            path = key.path(output_format="list")
            if len(path) == 1 and change == "dictionary_item_removed":
                continue
            nested_set(stored_learn, path, SIEVE_LEARN_VALUE_MASK)

    return stored_learn


def generate_controller_related_list(events: Iterable[APIEvent]) -> list[str]:
    api_event_list = list(events)
    controller_related_list: list[str] = []
    controller_related_uid_set: set[str | None] = set()
    for api_event in api_event_list:
        if api_event.rtype == "pod":
            pod_as_map = api_event.obj_map
            assert pod_as_map is not None
            if "labels" in pod_as_map and "sievetag" in pod_as_map["labels"]:
                controller_related_uid_set.add(pod_as_map["uid"])
                for owner_reference in pod_as_map["ownerReferences"]:
                    controller_related_uid_set.add(owner_reference["uid"])
    keep_tainting = True
    while keep_tainting:
        keep_tainting = False
        for api_event in api_event_list:
            if api_event.get_metadata_value("ownerReferences") is None:
                continue
            if api_event.get_metadata_value("uid") in controller_related_uid_set:
                for owner_reference in api_event.get_metadata_value("ownerReferences"):
                    if owner_reference["uid"] not in controller_related_uid_set:
                        controller_related_uid_set.add(owner_reference["uid"])
                        keep_tainting = True
            else:
                for owner_reference in api_event.get_metadata_value("ownerReferences"):
                    if owner_reference["uid"] in controller_related_uid_set:
                        controller_related_uid_set.add(
                            api_event.get_metadata_value("uid")
                        )
                        keep_tainting = True
    for api_event in api_event_list:
        if (
            api_event.get_metadata_value("uid") in controller_related_uid_set
            and api_event.key not in controller_related_list
        ):
            controller_related_list.append(api_event.key)
    controller_related_list.sort()
    return controller_related_list


def canonicalize_history_digest(first: Digest, second: Digest) -> Digest:
    can_history_digest = second_pass_learn_trim(first, second)

    def remove_ignored_value(event_map: Digest) -> None:
        ignored = set()
        for key in event_map:
            if event_map[key] == SIEVE_LEARN_VALUE_MASK:
                ignored.add(key)
            else:
                counts = event_map[key]
                assert isinstance(counts, dict)
                for etype in counts:
                    if counts[etype] == SIEVE_LEARN_VALUE_MASK:
                        ignored.add(key)
                        break
        for key in ignored:
            event_map.pop(key, None)

    remove_ignored_value(can_history_digest)
    return can_history_digest


def canonicalize_controller_related_list(
    first: Sequence[str], second: Sequence[str]
) -> list[str]:
    return sorted(set(first) | set(second))
