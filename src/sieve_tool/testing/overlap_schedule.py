"""Verify a bounded scheduling hold independently of application correctness."""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sieve_tool.events import ControllerRead
from sieve_tool.plans import DeliveryOverlap
from sieve_tool.testing.sequence_coverage import delivery_overlap
from sieve_tool.testing.sequences import PHASE_LABEL
from sieve_tool.trace import read_trace


def verify_scheduled_overlap(
    directory: Path, schedule: DeliveryOverlap
) -> dict[str, Any]:
    reasons: list[str] = []
    proof: dict[str, Any] = {
        "mode": schedule.mode,
        "verified": False,
        "reasons": reasons,
    }
    if schedule.mode == "observe":
        reasons.append("observe-only matched control has no injected timing fault")
        return proof
    try:
        for name in ("controller.log", "streamed-controller.log"):
            path = directory / name
            if path.is_file() and any(
                marker in path.read_text()
                for marker in (
                    "[SIEVE-TRACE-ERROR]",
                    "Sieve client RPC error:",
                    "Sieve client serialization error:",
                    "Sieve client configuration error:",
                )
            ):
                reasons.append("observer error invalidates scheduling evidence")
        observations = json.loads(
            (directory / "metadata-observations.json").read_text()
        )
        accepted = [
            r["response"] for r in observations["accepted"] if r["role"] == "subject"
        ]
        trace = directory / "sieve-server.log"
        coverage = delivery_overlap(trace, accepted=accepted)
        proof["coverage"] = asdict(coverage)
        if not coverage.verified:
            reasons.extend(coverage.reasons)
        states = []
        for position, line in enumerate(trace.read_text().splitlines()):
            marker = "[SIEVE-DELIVERY-OVERLAP]\t"
            if marker in line:
                state = json.loads(line.split(marker, 1)[1])
                states.append((position, state))
        if [s["state"] for _, s in states] != ["held", "released", "completed"]:
            raise ValueError(
                "one successful held/released/completed lifecycle is required"
            )
        (held_at, held), (released_at, released), (completed_at, completed) = states
        if (
            held.get("mode") != "hold"
            or held.get("resourceKey") != schedule.resource_key
            or held.get("reconciler") != schedule.reconciler
            or not held.get("uid")
            or not held.get("invocationID")
            or not held.get("instanceID")
            or any(
                released.get(k) != held.get(k) or completed.get(k) != held.get(k)
                for k in (
                    "mode",
                    "resourceKey",
                    "reconciler",
                    "invocationID",
                    "instanceID",
                    "uid",
                    "generation",
                    "resourceVersion",
                )
            )
            or any(
                completed.get(k) != released.get(k)
                for k in (
                    "releasedGeneration",
                    "releasedResourceVersion",
                )
            )
            or observations["delivery_overlap"]["held"] != held
            or observations["delivery_overlap"]["completed"] != completed
        ):
            raise ValueError("schedule identity or workload receipts do not match")
        witnesses = []
        records = {
            record.position: record.event for record in read_trace(trace).records
        }
        for witness in coverage.witnesses:
            old, newer = (
                accepted[witness["older_input"]],
                accepted[witness["delivered_input"]],
            )
            before, read, receipt, delivered, end = witness["positions"]
            read_event = records[read]
            if not isinstance(read_event, ControllerRead):
                continue
            read_object = read_event.key_to_obj.get(schedule.resource_key) or {}
            if (
                witness["reconciler"] == held["reconciler"]
                and witness["invocation_id"] == held["invocationID"]
                and witness["instance_id"] == held["instanceID"]
                and old["metadata"]["uid"] == newer["metadata"]["uid"] == held["uid"]
                and old["metadata"]["generation"] == held["generation"]
                and read_object.get("metadata", {}).get("resourceVersion")
                == held["resourceVersion"]
                and old["metadata"].get("labels", {}).get(PHASE_LABEL)
                == schedule.hold_phase
                and newer["metadata"].get("labels", {}).get(PHASE_LABEL)
                == schedule.release_phase
                and newer["metadata"]["generation"] == released["releasedGeneration"]
                and newer["metadata"]["resourceVersion"]
                == released["releasedResourceVersion"]
                and before
                < read
                < held_at
                < receipt
                < delivered
                < released_at
                < end
                < completed_at
            ):
                witnesses.append(witness)
        if not witnesses:
            reasons.append(
                "held invocation and exact release input lack a matching ordered witness"
            )
        proof.update(
            held=held, released=released, completed=completed, witnesses=witnesses
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        reasons.append("invalid scheduling evidence: " + str(error))
    proof["verified"] = not reasons
    return proof
