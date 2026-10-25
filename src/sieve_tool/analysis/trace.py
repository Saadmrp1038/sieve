"""Pair observations and retain the analyzer's established interval semantics."""

from copy import deepcopy
from dataclasses import dataclass

from sieve_tool.events import (
    ControllerHear,
    ControllerNonK8sWrite,
    ControllerRead,
    ControllerWrite,
    OperationID,
    ReconcileBegin,
    ReconcileEnd,
    ReconcilerEvent,
)
from sieve_tool.trace import Marker, Trace


@dataclass
class AnalyzedTrace:
    hears: list[ControllerHear]
    reconciles: list[ReconcilerEvent]


def assemble_trace(
    trace: Trace, *, compress_trivial: bool, loosen_boundary: bool
) -> AnalyzedTrace:
    heard: dict[int, ControllerHear] = {}
    finished_hears: set[int] = set()
    write_starts: dict[int, int] = {}
    finished_writes: set[int] = set()
    api_starts: dict[int, int] = {}
    reads: set[str] = set()
    read_types: set[str] = set()
    previous: dict[str, ReconcileBegin | None] = {}
    current: dict[str, ReconcileBegin] = {}
    trivial: dict[str, bool] = {}
    ongoing: set[str] = set()
    by_position: dict[int, ReconcilerEvent] = {}
    for record in trace.records:
        position = record.position
        event = deepcopy(record.event)
        match record.marker:
            case Marker.BEFORE_HEAR:
                assert isinstance(event, ControllerHear)
                if event.id in heard:
                    raise record.error(f"duplicate receipt start {event.id}")
                event.start_timestamp = position
                event.end_timestamp = trace.line_count
                heard[event.id] = event
            case Marker.AFTER_HEAR:
                assert isinstance(event, OperationID)
                if event.id not in heard or event.id in finished_hears:
                    raise record.error(f"unmatched or duplicate receipt end {event.id}")
                finished_hears.add(event.id)
                heard[event.id].end_timestamp = position
            case Marker.BEFORE_WRITE:
                assert isinstance(event, OperationID)
                if event.id in write_starts:
                    raise record.error(f"duplicate write start {event.id}")
                write_starts[event.id] = position
            case Marker.AFTER_WRITE:
                assert isinstance(event, ControllerWrite)
                if event.id not in write_starts or event.id in finished_writes:
                    raise record.error(f"unmatched or duplicate write end {event.id}")
                finished_writes.add(event.id)
                for key in trivial:
                    trivial[key] = False
                if event.reconcile_fun not in current and not loosen_boundary:
                    continue
                event.read_keys = reads.copy()
                event.read_types = read_types.copy()
                event.start_timestamp = write_starts[event.id]
                event.end_timestamp = position
                if event.reconcile_fun in current:
                    prev = previous[event.reconcile_fun]
                    event.set_range(
                        prev.end_timestamp if prev is not None else -1, position
                    )
                    event.reconcile_id = current[event.reconcile_fun].reconcile_id
                by_position[event.start_timestamp] = event
            case Marker.BEFORE_API:
                assert isinstance(event, OperationID)
                api_starts[event.id] = position
            case Marker.AFTER_API:
                assert isinstance(event, ControllerNonK8sWrite)
                for key in trivial:
                    trivial[key] = False
                if event.reconcile_fun not in current and not loosen_boundary:
                    continue
                if event.id not in api_starts:
                    raise record.error(f"annotated call {event.id} has no start")
                event.start_timestamp = api_starts[event.id]
                event.end_timestamp = position
                if event.reconcile_fun in current:
                    # The previous engine called a missing set_range method here.
                    # Supporting this path would change behavior, not just types.
                    raise record.error(
                        "annotated writes within reconciliation have no supported range semantics"
                    )
                by_position[event.start_timestamp] = event
            case Marker.CACHE_READ | Marker.REST_READ:
                assert isinstance(event, ControllerRead)
                if event.reconcile_fun not in current:
                    continue
                event.end_timestamp = position
                event.reconcile_id = current[event.reconcile_fun].reconcile_id
                by_position[position] = event
                if record.marker == Marker.CACHE_READ:
                    if event.etype == "Get":
                        reads.update(event.key_set)
                    else:
                        read_types.add(event.rtype)
            case Marker.BEFORE_RECONCILE:
                assert isinstance(event, ReconcileBegin)
                fun = event.reconcile_fun
                if fun in ongoing:
                    raise record.error(
                        f"overlapping workers for {fun} are not supported"
                    )
                ongoing.add(fun)
                event.end_timestamp = position
                by_position[position] = event
                if fun not in current:
                    previous[fun] = None
                    current[fun] = event
                elif not compress_trivial or not trivial[fun]:
                    previous[fun] = current[fun]
                    current[fun] = event
                trivial[fun] = True
            case Marker.AFTER_RECONCILE:
                assert isinstance(event, ReconcileEnd)
                if event.reconcile_fun not in ongoing:
                    raise record.error(
                        f"reconcile end without an active {event.reconcile_fun}"
                    )
                ongoing.remove(event.reconcile_fun)
                event.end_timestamp = position
                by_position[position] = event
                if not ongoing:
                    reads = set()
                    read_types = set()
    return AnalyzedTrace(
        list(heard.values()), [event for _, event in sorted(by_position.items())]
    )
