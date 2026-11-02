"""Prove update-delivery overlap from one ordered, identity-bearing Sieve trace."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sieve_tool.events import (
    ControllerHear,
    ControllerRead,
    OperationID,
    ReconcileBegin,
    ReconcileEnd,
)
from sieve_tool.testing.child_repair import Resource
from sieve_tool.testing.sequences import PHASE_LABEL
from sieve_tool.trace import Marker, read_trace


@dataclass(frozen=True)
class OverlapCoverage:
    verified: bool
    reasons: tuple[str, ...]
    witnesses: tuple[dict[str, Any], ...]
    observed_inputs: tuple[int, ...] = ()
    delivered_inputs: tuple[int, ...] = ()


def _same_input(
    observed: Resource | None, accepted: Resource, *, exact_rv: bool
) -> bool:
    if observed is None:
        return False
    left, right = observed.get("metadata", {}), accepted["metadata"]
    return (
        all(
            left.get(k) == right.get(k)
            for k in ("uid", "name", "namespace", "generation")
        )
        and (
            not exact_rv or left.get("resourceVersion") == right.get("resourceVersion")
        )
        and left.get("labels", {}).get(PHASE_LABEL)
        == right.get("labels", {}).get(PHASE_LABEL)
        and observed.get("spec") == accepted.get("spec")
    )


def delivery_overlap(path: Path, *, accepted: list[Resource]) -> OverlapCoverage:
    """Require begin < old-input Get < new full-informer receipt/end < end.

    The receipt must match an acknowledged input's UID/generation/spec/RV/phase.
    This proves delivery during an invocation that read an older input. It does
    not establish when the API acknowledgement occurred relative to begin, nor
    concurrent processing of the same workqueue key.
    """
    with path.open() as stream:
        if any(
            "[SIEVE-TRACE-ERROR]" in line or "Sieve client RPC error:" in line
            for line in stream
        ):
            return OverlapCoverage(False, ("observer_error",), ())
    if not 2 <= len(accepted) <= 4:
        return OverlapCoverage(False, ("invalid_accepted_history",), ())
    identity = tuple(
        accepted[0].get("metadata", {}).get(k) for k in ("uid", "namespace", "name")
    )
    previous = None
    versions = set()
    for response in accepted:
        metadata = response.get("metadata", {})
        generation = metadata.get("generation")
        version = metadata.get("resourceVersion")
        if (
            any(not isinstance(v, str) or not v for v in identity)
            or tuple(metadata.get(k) for k in ("uid", "namespace", "name")) != identity
            or not isinstance(response.get("kind"), str)
            or response["kind"] != accepted[0].get("kind")
            or type(generation) is not int
            or generation < 1
            or previous is not None
            and generation != previous + 1
            or not isinstance(version, str)
            or not version
            or version in versions
            or not isinstance(response.get("spec"), dict)
        ):
            return OverlapCoverage(False, ("invalid_accepted_history",), ())
        previous = generation
        versions.add(version)
    active: dict[tuple[str, str], dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    hears: dict[int, tuple[int, int]] = {}
    witnesses = []
    observed_inputs: set[int] = set()
    delivered_inputs: set[int] = set()
    invalid = False
    for record in read_trace(path).records:
        event, position = record.event, record.position
        if isinstance(event, ReconcileBegin):
            if event.invocation_id is None or not event.instance_id:
                continue
            key = (event.reconcile_fun, event.invocation_id)
            if key in seen:
                invalid = True
            seen.add(key)
            active[key] = {
                "begin": position,
                "instance": event.instance_id,
                "reads": [],
                "deliveries": [],
            }
        elif (
            isinstance(event, ControllerRead)
            and event.etype == "Get"
            and event.error == "NoError"
            and event.invocation_id is not None
        ):
            scope = active.get((event.reconcile_fun, event.invocation_id))
            if scope is not None:
                for index, response in enumerate(accepted):
                    metadata = response["metadata"]
                    resource_key = f"{response['kind'].lower()}/{metadata['namespace']}/{metadata['name']}"
                    if _same_input(
                        event.key_to_obj.get(resource_key), response, exact_rv=False
                    ):
                        scope["reads"].append((position, index))
                        observed_inputs.add(index)
        elif (
            isinstance(event, ControllerHear)
            and event.observed_by == "informer"
            and event.etype == "Updated"
        ):
            for index, response in enumerate(accepted):
                metadata = response["metadata"]
                if (
                    event.key
                    == f"{response['kind'].lower()}/{metadata['namespace']}/{metadata['name']}"
                    and _same_input(event.obj_map, response, exact_rv=True)
                ):
                    if event.id in hears:
                        invalid = True
                    hears[event.id] = (position, index)
        elif (
            record.marker == Marker.AFTER_HEAR
            and isinstance(event, OperationID)
            and event.id in hears
        ):
            start, index = hears.pop(event.id)
            delivered_inputs.add(index)
            for scope in active.values():
                for read, previous in scope["reads"]:
                    if read < start and previous < index:
                        scope["deliveries"].append(
                            (read, start, position, previous, index)
                        )
        elif isinstance(event, ReconcileEnd) and event.invocation_id is not None:
            key = (event.reconcile_fun, event.invocation_id)
            scope = active.pop(key, None)
            if scope is None or scope["instance"] != event.instance_id:
                invalid = True
                continue
            for read, start, delivered, previous, index in scope["deliveries"]:
                witnesses.append(
                    {
                        "invocation_id": event.invocation_id,
                        "instance_id": event.instance_id,
                        "reconciler": event.reconcile_fun,
                        "older_input": previous,
                        "delivered_input": index,
                        "positions": [scope["begin"], read, start, delivered, position],
                    }
                )
    if invalid:
        return OverlapCoverage(False, ("invalid_invocation_identity",), ())
    return OverlapCoverage(
        bool(witnesses),
        () if witnesses else ("delivery_overlap_unproven",),
        tuple(witnesses),
        tuple(sorted(observed_inputs)),
        tuple(sorted(delivered_inputs)),
    )


def main(arguments: Sequence[str] | None = None) -> int:
    """Review a complete collected trace; this does not certify application health."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.output.exists() or args.output.is_symlink():
        parser.error("coverage output already exists; choose a fresh output")
    try:
        observations = json.loads(args.observations.read_text())
        accepted = [
            row["response"]
            for row in observations["accepted"]
            if row["role"] == "subject"
        ]
        coverage = delivery_overlap(args.trace, accepted=accepted)
        report = {"schema_version": 1, **asdict(coverage)}
    except (ValueError, KeyError, TypeError, OSError) as error:
        report = {
            "schema_version": 1,
            "verified": False,
            "reasons": ["invalid_coverage_evidence"],
            "witnesses": [],
            "error": str(error),
        }
    report["evidence_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (args.trace, args.observations)
        if p.is_file()
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
