"""Decode Sieve's existing tab-delimited records with source locations."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sieve_tool.events import (
    APIEvent,
    ControllerHear,
    ControllerNonK8sWrite,
    ControllerRead,
    ControllerWrite,
    Event,
    Object,
    OperationID,
    ReconcileBegin,
    ReconcileEnd,
    extract_name,
    extract_namespace_name,
    generate_key,
)


class Marker(StrEnum):
    BEFORE_HEAR = "[SIEVE-BEFORE-HEAR]"
    AFTER_HEAR = "[SIEVE-AFTER-HEAR]"
    BEFORE_WRITE = "[SIEVE-BEFORE-REST-WRITE]"
    AFTER_WRITE = "[SIEVE-AFTER-REST-WRITE]"
    REST_READ = "[SIEVE-AFTER-REST-READ]"
    CACHE_READ = "[SIEVE-AFTER-CACHE-READ]"
    BEFORE_RECONCILE = "[SIEVE-BEFORE-RECONCILE]"
    AFTER_RECONCILE = "[SIEVE-AFTER-RECONCILE]"
    BEFORE_API = "[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]"
    AFTER_API = "[SIEVE-AFTER-ANNOTATED-API-INVOCATION]"
    API_EVENT = "[SIEVE-API-EVENT]"


type Payload = Event | APIEvent | OperationID


class TraceError(ValueError):
    def __init__(self, source: str | Path, position: int, marker: Marker, problem: str):
        self.source = str(source)
        self.position = position
        self.marker = marker
        super().__init__(f"{source}:{position + 1}: {marker}: {problem}")


@dataclass(frozen=True)
class TraceRecord:
    source: str
    position: int
    marker: Marker
    event: Payload

    def error(self, problem: str) -> TraceError:
        return TraceError(self.source, self.position, self.marker, problem)


@dataclass(frozen=True)
class Trace:
    records: tuple[TraceRecord, ...]
    line_count: int


def _object(value: str) -> Object | None:
    decoded = json.loads(value)
    if decoded is not None and not isinstance(decoded, dict):
        raise ValueError("expected a JSON object or null")
    return decoded


def _required_object(value: str) -> Object:
    result = _object(value)
    if result is None:
        raise ValueError("expected a JSON object")
    return result


def _count(fields: list[str], expected: int) -> None:
    if len(fields) != expected:
        raise ValueError(f"expected {expected - 1} fields, found {len(fields) - 1}")


def _read(fields: list[str], cached: bool) -> ControllerRead:
    # The old decoder passed booleans to a string comparison, yielding False
    # for both sources. Keep that decoded field; Marker records provenance.
    if cached:
        operation = fields[1]
        if operation == "Get":
            _count(fields, 8)
            rtype, namespace, name, reconciler, error, raw = fields[2:]
        elif operation == "List":
            _count(fields, 6)
            rtype, reconciler, error, raw = fields[2:]
            namespace, name = "", ""
        else:
            raise ValueError(f"unsupported cache read {operation!r}")
    else:
        _count(fields, 9)
        _, operation, reconciler, error, rtype, namespace, name, raw = fields[1:]
        if operation not in {"Get", "List"}:
            raise ValueError(f"unsupported REST read {operation!r}")
        if operation == "List":
            namespace, name = "", ""
    objects: dict[str, Object | None] = {}
    if operation == "Get":
        objects[generate_key(rtype, namespace, name)] = _object(raw)
    else:
        decoded = json.loads(raw)
        if decoded is not None:
            if not isinstance(decoded, list):
                raise ValueError("List read requires a JSON array or null")
            for obj in decoded:
                if not isinstance(obj, dict):
                    raise ValueError("List entries must be objects")
                ns, obj_name = extract_namespace_name(obj)
                key = generate_key(rtype, ns, obj_name)
                if key in objects:
                    raise ValueError(f"duplicate List entry {key}")
                objects[key] = obj
    return ControllerRead(
        operation, False, rtype, reconciler, error, objects, set(objects)
    )


def _payload(marker: Marker, fields: list[str]) -> Payload:
    match marker:
        case Marker.BEFORE_WRITE | Marker.AFTER_HEAR | Marker.BEFORE_API:
            _count(fields, 2)
            return OperationID(int(fields[1]))
        case Marker.BEFORE_HEAR:
            _count(fields, 5)
            obj = _required_object(fields[4])
            extract_namespace_name(obj)
            return ControllerHear(int(fields[1]), fields[2], fields[3], fields[4], obj)
        case Marker.AFTER_WRITE:
            _count(fields, 9)
            if fields[2] == "DeleteAllOf":
                raise ValueError("DeleteAllOf writes are not supported by analysis")
            written = _object(fields[8])
            name = fields[7] if fields[7] else extract_name(written)
            return ControllerWrite(
                int(fields[1]),
                fields[2],
                fields[3],
                fields[4],
                fields[5],
                fields[6],
                name,
                fields[8],
                written,
            )
        case Marker.REST_READ | Marker.CACHE_READ:
            return _read(fields, marker == Marker.CACHE_READ)
        case Marker.AFTER_API:
            _count(fields, 7)
            module, path, receiver, function, reconciler = fields[2:]
            return ControllerNonK8sWrite(
                int(fields[1]), module, path, receiver, function, reconciler
            )
        case Marker.BEFORE_RECONCILE | Marker.AFTER_RECONCILE:
            _count(fields, 3)
            if marker == Marker.BEFORE_RECONCILE:
                return ReconcileBegin(fields[1], fields[2])
            return ReconcileEnd(fields[1], fields[2])
        case Marker.API_EVENT:
            _count(fields, 7)
            # API records have six fields following the marker.
            etype, original_key, rtype, namespace, name, raw = fields[1:]
            return APIEvent(
                etype, original_key, rtype, namespace, name, raw, _required_object(raw)
            )


def decode_line(
    line: str,
    source: str | Path,
    position: int,
    markers: frozenset[Marker] | None = None,
) -> TraceRecord | None:
    candidates = [(line.index(marker), marker) for marker in Marker if marker in line]
    if not candidates:
        return None
    offset, marker = min(candidates)
    if markers is not None and marker not in markers:
        return None
    fields = line[offset:].strip("\n").split("\t")
    try:
        event = _payload(marker, fields)
    except (ValueError, KeyError, TypeError, AssertionError, IndexError) as error:
        identity = f"event {fields[1]}: " if len(fields) > 1 else ""
        raise TraceError(source, position, marker, identity + str(error)) from error
    return TraceRecord(str(source), position, marker, event)


def iter_records(
    path: str | Path, markers: frozenset[Marker] | None = None
) -> Iterator[TraceRecord]:
    source = Path(path)
    with source.open() as stream:
        for position, line in enumerate(stream):
            record = decode_line(line, source, position, markers)
            if record is not None:
                yield record


def read_trace(path: str | Path) -> Trace:
    source = Path(path)
    records = []
    line_count = 0
    with source.open() as stream:
        for position, line in enumerate(stream):
            line_count = position + 1
            record = decode_line(line, source, position)
            if record is not None:
                records.append(record)
    return Trace(tuple(records), line_count)


def iter_api_events(path: str | Path) -> Iterator[APIEvent]:
    for record in iter_records(path, frozenset({Marker.API_EVENT})):
        assert isinstance(record.event, APIEvent)
        yield record.event
