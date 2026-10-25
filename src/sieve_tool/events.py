"""Event data and resource comparisons; trace I/O lives in sieve_tool.trace."""

import json
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

from sieve_tool.common.event_delta import conflicting_event_payload

# Resource schemas are supplied by the operator under test, not by Sieve.
type Object = dict[str, Any]
type Mask = dict[str, list[list[str]]]
type ReconcileID = str | int


HEAR_READ_FILTER_FLAG = True
ERROR_MSG_FILTER_FLAG = True


ALLOWED_ERROR_TYPE = ["NoError"]

EVENT_NONE_TYPE = "NONE_TYPE"
NON_K8S_WRITE = "NON_K8S_WRITE"

DEFAULT_NS = "default"

UNKNOWN_RECONCILE_FUN = "unknown"


class APIEventTypes:
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class ControllerHearTypes:
    ADDED = "Added"
    UPDATED = "Updated"
    DELETED = "Deleted"
    REPLACED = "Replaced"  # Replaced is emitted when we encountered watch errors and had to do a relist
    SYNC = "Sync"  # Sync is for synthetic events during a periodic resync


class ControllerWriteTypes:
    CREATE = "Create"
    UPDATE = "Update"
    DELETE = "Delete"
    DELETEALLOF = "DeleteAllOf"
    PATCH = "Patch"
    STATUS_UPDATE = "StatusUpdate"
    STATUS_PATCH = "StatusPatch"


# We do not include Sync and Replaced here
detectable_controller_hear_types = [
    ControllerHearTypes.ADDED,
    ControllerHearTypes.UPDATED,
    ControllerHearTypes.DELETED,
]

detectable_controller_write_types = [
    ControllerWriteTypes.CREATE,
    ControllerWriteTypes.UPDATE,
    ControllerWriteTypes.DELETE,
    ControllerWriteTypes.PATCH,
    ControllerWriteTypes.STATUS_UPDATE,
    ControllerWriteTypes.STATUS_PATCH,
]


def consistent_event_type(
    controller_hear_type: str, controller_write_type: str
) -> bool:
    both_create = (
        controller_hear_type == ControllerHearTypes.ADDED
        and controller_write_type == ControllerWriteTypes.CREATE
    )
    both_update = controller_hear_type == ControllerHearTypes.UPDATED and (
        controller_write_type == ControllerWriteTypes.UPDATE
        or controller_write_type == ControllerWriteTypes.PATCH
        or controller_write_type == ControllerWriteTypes.STATUS_UPDATE
        or controller_write_type == ControllerWriteTypes.STATUS_PATCH
    )
    both_delete = (
        controller_hear_type == ControllerHearTypes.DELETED
        and controller_write_type == ControllerWriteTypes.DELETE
    )
    return both_create or both_update or both_delete


def conflicting_event_type(
    prev_controller_hear_type: str, cur_controller_hear_type: str
) -> bool:
    other_then_delete = (
        prev_controller_hear_type != ControllerHearTypes.DELETED
        and cur_controller_hear_type == ControllerHearTypes.DELETED
    )
    delete_then_other = (
        prev_controller_hear_type == ControllerHearTypes.DELETED
        and cur_controller_hear_type != ControllerHearTypes.DELETED
    )
    return other_then_delete or delete_then_other


def _metadata(obj: Object | None) -> Object:
    if obj is None:
        raise ValueError("object metadata must be an object")
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("object metadata must be an object")
    return metadata


def _text(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def extract_uid(obj: Object) -> str | None:
    value = _metadata(obj).get("uid")
    return _text(value, "metadata.uid") if value is not None else None


def extract_namespace_name(obj: Object) -> tuple[str, str]:
    meta = _metadata(obj)
    return _text(meta.get("namespace", DEFAULT_NS), "metadata.namespace"), _text(
        meta["name"], "metadata.name"
    )


def extract_name(obj: Object | None) -> str:
    return _text(_metadata(obj)["name"], "metadata.name")


def extract_generate_name(obj: Object | None) -> str | None:
    if obj is None:
        raise ValueError("generateName is unavailable on a null object")
    meta = obj["metadata"] if "metadata" in obj else obj
    if not isinstance(meta, dict):
        raise ValueError("object metadata must be an object")
    value = meta.get("generateName")
    return _text(value, "generateName") if value is not None else None


def is_generated_random_name(name: str, generate_name: str) -> bool:
    return name.startswith(generate_name) and len(name) == len(generate_name) + 5


def generate_key(resource_type: str, namespace: str, name: str) -> str:
    return "/".join([resource_type, namespace, name])


def parse_key(key: str) -> tuple[str, str, str]:
    tokens = key.split("/")
    assert len(tokens) == 3
    return tokens[0], tokens[1], tokens[2]


def get_mask_by_resource_key(key_mask_map: Mask, resource_key: str) -> list[str]:
    # TODO: converting the list to a string may lead to ambiguity
    # consider two lists: ["a", "b", "c"] and ["a/b", "c"]
    # after converting to string they look the same
    masked_keys = []
    for key in key_mask_map:
        if key == resource_key or PurePath("/" + resource_key).match("/" + key):
            for field_path_list in key_mask_map[key]:
                assert isinstance(field_path_list, list)
                assert len(field_path_list) > 0
                if len(field_path_list) == 1:
                    masked_keys.append(field_path_list[0])
                else:
                    masked_keys.append("/".join(field_path_list))
    return masked_keys


@dataclass
class APIEvent:
    etype: str
    original_key: str
    rtype: str
    namespace: str
    name: str
    obj_str: str
    obj_map: Object | None

    @property
    def key(self) -> str:
        return generate_key(self.rtype, self.namespace, self.name)

    def get_metadata_value(self, key: str) -> Any:
        if self.obj_map is None:
            return None
        if key in self.obj_map:
            return self.obj_map[key]
        return self.obj_map.get("metadata", {}).get(key)


@dataclass
class ControllerHear:
    id: int
    etype: str
    rtype: str
    obj_str: str
    obj_map: Object
    start_timestamp: int = -1
    end_timestamp: int = -1
    slim_prev_obj_map: Object | None = None
    slim_cur_obj_map: Object | None = None
    prev_etype: str = EVENT_NONE_TYPE
    cancelled_by: set[int] = field(default_factory=set)
    signature_counter: int = 1
    namespace: str = field(init=False)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.namespace, self.name = extract_namespace_name(self.obj_map)

    @property
    def key(self) -> str:
        return generate_key(self.rtype, self.namespace, self.name)


@dataclass
class ControllerNonK8sWrite:
    id: int
    module: str
    file_path: str
    recv_type: str
    fun_name: str
    reconcile_fun: str
    reconcile_id: ReconcileID = -1
    start_timestamp: int = -1
    end_timestamp: int = -1
    range_start_timestamp: int = -1
    range_end_timestamp: int = -1
    signature_counter: int = 1


@dataclass
class ControllerWrite:
    id: int
    etype: str
    reconcile_fun: str
    error: str
    rtype: str
    namespace: str
    name: str
    obj_str: str
    obj_map: Object | None
    reconcile_id: ReconcileID = -1
    start_timestamp: int = -1
    end_timestamp: int = -1
    range_start_timestamp: int = -1
    range_end_timestamp: int = -1
    read_types: set[str] = field(default_factory=set)
    read_keys: set[str] = field(default_factory=set)
    prev_obj_map: Object | None = None
    slim_prev_obj_map: Object | None = None
    slim_cur_obj_map: Object | None = None
    prev_etype: str = EVENT_NONE_TYPE
    signature_counter: int = 1

    @property
    def key(self) -> str:
        return generate_key(self.rtype, self.namespace, self.name)

    def set_range(self, start_timestamp: int, end_timestamp: int) -> None:
        if start_timestamp >= end_timestamp:
            raise ValueError("write range must end after it starts")
        self.range_start_timestamp = start_timestamp
        self.range_end_timestamp = end_timestamp


@dataclass
class ControllerRead:
    etype: str
    from_cache: bool
    rtype: str
    reconcile_fun: str
    error: str
    key_to_obj: dict[str, Object | None]
    key_set: set[str]
    reconcile_id: ReconcileID = -1
    end_timestamp: int = -1


@dataclass(frozen=True)
class OperationID:
    id: int


@dataclass
class ReconcileBegin:
    reconcile_fun: str
    reconcile_id: str
    end_timestamp: int = -1


@dataclass
class ReconcileEnd:
    reconcile_fun: str
    reconcile_id: str
    end_timestamp: int = -1


type ReconcilerEvent = (
    ControllerWrite
    | ControllerNonK8sWrite
    | ControllerRead
    | ReconcileBegin
    | ReconcileEnd
)
type Event = ControllerHear | ReconcilerEvent


def conflicting_event(
    prev_controller_hear: ControllerHear,
    cur_controller_hear: ControllerHear,
    masked_keys: set[str],
    masked_paths: set[str],
) -> bool:
    if conflicting_event_type(prev_controller_hear.etype, cur_controller_hear.etype):
        return True
    elif (
        prev_controller_hear.etype != ControllerHearTypes.DELETED
        and cur_controller_hear.etype != ControllerHearTypes.DELETED
        and conflicting_event_payload(
            prev_controller_hear.slim_cur_obj_map,
            cur_controller_hear.obj_map,
            masked_keys,
            masked_paths,
        )
    ):
        return True
    return False


def is_creation_or_deletion(etype: str) -> bool:
    is_hear_creation_or_deletion = (
        etype == ControllerHearTypes.ADDED or etype == ControllerHearTypes.DELETED
    )
    is_write_creation_or_deletion = (
        etype == ControllerWriteTypes.CREATE or etype == ControllerWriteTypes.DELETE
    )
    return is_hear_creation_or_deletion or is_write_creation_or_deletion


def get_event_signature(event: ControllerHear | ControllerWrite) -> str:
    assert isinstance(event, ControllerHear) or isinstance(event, ControllerWrite)
    signature = (
        event.etype
        if is_creation_or_deletion(event.etype)
        else "\t".join(
            [
                event.etype,
                json.dumps(event.slim_prev_obj_map, sort_keys=True),
                json.dumps(event.slim_cur_obj_map, sort_keys=True),
            ]
        )
    )
    return signature
