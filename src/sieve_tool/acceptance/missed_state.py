"""Verify a missed intermediate state using receipts and returned cache values."""

import json
from dataclasses import dataclass
from pathlib import Path

from sieve_tool.events import ControllerRead, Object
from sieve_tool.oracle.artifacts import ArtifactError
from sieve_tool.plans import (
    FaultPlan,
    PauseControllerAction,
    ResumeControllerAction,
    StateMatchCondition,
)
from sieve_tool.testing.updates import contains_state, read_updates
from sieve_tool.trace import Marker, TraceError, iter_records


@dataclass(frozen=True)
class MissedStateProof:
    verified: bool
    reasons: tuple[str, ...]
    resource_key: str = ""
    consumer: str = ""
    held_reads: int = 0
    released_reads: int = 0
    returned_versions: tuple[str, ...] = ()
    intermediate_state: Object | None = None
    later_state: Object | None = None


def verify_missed_state(directory: Path, plan: FaultPlan) -> MissedStateProof:
    try:
        return _verify(directory, plan)
    except ArtifactError, TraceError:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        return MissedStateProof(
            False, ("invalid missed-state evidence: " + str(error),)
        )


def _verify(directory: Path, plan: FaultPlan) -> MissedStateProof:
    if plan.actions is None or len(plan.actions) != 2:
        raise ValueError("expected one pause and one resume action")
    pause, resume = plan.actions
    if (
        not isinstance(pause, PauseControllerAction)
        or not isinstance(resume, ResumeControllerAction)
        or pause.pause_at != "beforeControllerRead"
        or not pause.pause_by
    ):
        raise ValueError("expected a cache Get pause scoped to one reconciler")
    start = pause.trigger.definitions[0].condition
    if not isinstance(start, StateMatchCondition):
        raise ValueError("pause must identify an intermediate input state")
    intermediate = json.loads(start.state_match)
    definitions = resume.trigger.definitions
    states = [
        json.loads(d.condition.state_match)
        for d in definitions
        if isinstance(d.condition, StateMatchCondition)
    ]
    if (
        not states
        or len(states) != len(definitions)
        or any(s != states[0] for s in states)
    ):
        raise ValueError("resume must identify one later state in all cache streams")
    later = states[0]
    observers = {
        d.observation_point.by
        for d in definitions
        if d.observation_point is not None
        and d.observation_point.when == "afterControllerRecv"
    }
    if len(observers) != len(definitions) or resume.trigger.expression != "&".join(
        d.trigger_name for d in definitions
    ):
        raise ValueError("resume must wait for every cache stream")
    source = read_updates(directory / "workload-updates.json")
    if (
        source.workload != plan.workload
        or source.resource_key != pause.pause_scope
        or resume.pause_scope != pause.pause_scope
    ):
        raise ValueError("input evidence belongs to a different workload/resource")
    index = next(
        (i for i, u in enumerate(source.updates) if u.state == intermediate), -1
    )
    if (
        index < 1
        or index >= len(source.updates) - 1
        or source.updates[index + 1].state != later
    ):
        raise ValueError("independent input sequence does not contain this overwrite")
    accepted = source.updates[index : index + 2]
    uid = accepted[0].response["metadata"]["uid"]
    log_file = directory / "sieve-server.log"
    lines = log_file.read_text().splitlines()
    suffix = pause.pause_at + " " + pause.pause_scope
    holds = [
        i
        for i, line in enumerate(lines)
        if line.endswith(": Pause controller at " + suffix)
    ]
    releases = [
        i
        for i, line in enumerate(lines)
        if line.endswith(": End pause controller at " + suffix)
    ]
    receipts: list[tuple[int, str, Object]] = []
    completions = []
    for position, line in enumerate(lines):
        fields = line.split("\t")
        if (
            fields[0].endswith("NotifyTestAfterControllerRecv")
            and len(fields) == 5
            and fields[2] == source.resource_key
        ):
            body = json.loads(fields[3])
            if isinstance(body, dict) and body.get("metadata", {}).get("uid") == uid:
                receipts.append((position, fields[4], body))
        if (
            fields[0].endswith("NotifyTestAfterControllerReadPause")
            and len(fields) >= 3
            and fields[1] == "Get"
            and fields[2] == source.resource_key
        ):
            completions.append(position)
    reads: list[tuple[int, ControllerRead]] = []
    for record in iter_records(log_file, frozenset({Marker.CACHE_READ})):
        event = record.event
        if (
            isinstance(event, ControllerRead)
            and event.etype == "Get"
            and event.reconcile_fun == pause.pause_by
            and source.resource_key in event.key_to_obj
        ):
            reads.append((record.position, event))
    reasons = []
    if not holds or len(holds) != len(releases):
        reasons.append("matching read hold/release is missing")
    if any(
        contains_state(r.key_to_obj[source.resource_key], intermediate)
        for _, r in reads
    ):
        reasons.append("the paused consumer read the intermediate state")
    returned = []
    for held, released in zip(holds, releases, strict=False):
        if released <= held:
            reasons.append("read release precedes its hold")
            continue
        for observer in observers:
            b = [
                p
                for p, o, value in receipts
                if o == observer and accepted[0].matches(value) and p < released
            ]
            c = [
                p
                for p, o, value in receipts
                if o == observer and accepted[1].matches(value) and held < p < released
            ]
            if not b or not c or max(b) >= min(c):
                reasons.append("overwrite during the hold is unproven for " + observer)
        # End-pause is logged by the before-read RPC. The Get then executes;
        # its value is recorded before the deferred after-read RPC unlocks it.
        end = next((p for p in completions if p > released), None)
        if end is None:
            reasons.append("held Get has no closing after-read hook")
            continue
        actual = [(p, r) for p, r in reads if released < p < end]
        if len(actual) != 1:
            reasons.append("held Get has no unambiguous returned value")
            continue
        read = actual[0][1]
        value = read.key_to_obj[source.resource_key]
        if (
            read.error != "NoError"
            or value is None
            or value.get("metadata", {}).get("uid") != uid
            or not any(u.matches(value) for u in source.updates[index + 1 :])
        ):
            reasons.append(
                "held Get did not return an independently produced later state"
            )
            continue
        returned.append(str(value["metadata"].get("resourceVersion", "")))
    return MissedStateProof(
        not reasons,
        tuple(reasons),
        source.resource_key,
        pause.pause_by,
        len(holds),
        len(releases),
        tuple(returned),
        intermediate,
        later,
    )
