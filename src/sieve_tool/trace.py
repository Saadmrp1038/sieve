"""Decode Sieve's existing tab-delimited records with source locations."""

import ast
import json
import re
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
            if len(fields) not in {5, 6}:
                raise ValueError("expected 4 fields and an optional informer observer")
            observer = fields[5] if len(fields) == 6 else "informer"
            if observer not in {"informer", "metadata-informer"}:
                raise ValueError("unsupported informer observer")
            obj = _required_object(fields[4])
            extract_namespace_name(obj)
            return ControllerHear(
                int(fields[1]),
                fields[2],
                fields[3],
                fields[4],
                obj,
                observed_by=observer,
            )
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


_KLOG_QUOTED_MESSAGE = re.compile(
    r'^[IWEF]\d{4} \d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+.+\]\s+"$'
)
_GO_LOG_STRING = re.compile(
    r'"(?:[^"\\\r\n]|\\(?:[abfnrtv"\\]|x[0-7][0-9a-fA-F]|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|[01][0-7]{2}))*"'
)


def _quoted_message(line: str, offset: int) -> str:
    literal = _GO_LOG_STRING.match(line, offset - 1)
    if literal is None:
        raise ValueError("malformed quoted log message")
    try:
        message = json.loads(literal.group())
    except json.JSONDecodeError:
        # Go's quoting also permits ASCII hex/octal escapes. The lexer limits
        # this fallback to a string literal; no expressions are evaluated.
        try:
            message = ast.literal_eval(literal.group())
        except ValueError, SyntaxError:
            raise ValueError("invalid Go quoted log message") from None
    if not isinstance(message, str):
        raise ValueError("quoted log message must be a string")
    return message


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
    fields: list[str] = []
    try:
        message = line[offset:].strip("\n")
        if _KLOG_QUOTED_MESSAGE.fullmatch(line[:offset]):
            # Recent Kubernetes redirects standard log messages through klog's
            # quoted text format. Decode that envelope once, leaving JSON
            # payload escapes for the existing event decoder.
            message = _quoted_message(line, offset)
        fields = message.strip("\n").split("\t")
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
