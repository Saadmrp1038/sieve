"""Run bounded metadata entry lifetimes and compare with a fresh final state."""

import argparse
import copy
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from sieve_tool.plans import FaultPlan, write_plan
from sieve_tool.process import ProcessTimeout, deadline, execution
from sieve_tool.testing.child_repair import Resource, Wait, get, kubectl
from sieve_tool.testing.semantic import (
    HistoryEquivalenceMonitor,
    LatestStateMonitor,
    MetadataCleanupMonitor,
    SemanticVerdict,
    StabilityWindow,
)
from sieve_tool.testing.sequence_workload import (
    ControllerConfigurationError,
    SemanticViolation,
    SequenceObservation,
)
from sieve_tool.testing.sequences import (
    HISTORY_KEY,
    KEEP_KEY,
    PHASE_LABEL,
    MetadataStep,
    metadata_histories,
)


class MetadataTarget(Protocol):
    operator: str
    parent_kind: str
    metadata_path: str
    worker_argument: str
    effective_value: str

    def setup(
        self, wait: Wait, *, name: str, metadata: dict[str, dict[str, str]]
    ) -> Resource: ...

    def observe(self, name: str) -> SequenceObservation: ...


def _patch(
    target: MetadataTarget,
    accepted: Resource,
    channel: str,
    step: MetadataStep,
    index: int,
) -> Resource:
    phase = f"metadata-{index}-{step.operation}"
    labels = {**accepted["metadata"].get("labels", {}), PHASE_LABEL: phase}
    path = target.metadata_path + "/" + channel + "/" + HISTORY_KEY.replace("/", "~1")
    change: Resource = {
        "op": {"add": "add", "change": "replace", "remove": "remove"}[step.operation],
        "path": path,
    }
    if step.value is not None:
        change["value"] = step.value
    response: Resource = json.loads(
        kubectl(
            "patch",
            target.parent_kind,
            accepted["metadata"]["name"],
            "--type=json",
            "-p",
            json.dumps(
                [
                    {
                        "op": "test",
                        "path": "/metadata/uid",
                        "value": accepted["metadata"]["uid"],
                    },
                    {"op": "test", "path": "/spec", "value": accepted["spec"]},
                    {"op": "add", "path": "/metadata/labels", "value": labels},
                    change,
                ]
            ),
            "-o",
            "json",
        )
    )
    expected_spec = copy.deepcopy(accepted["spec"])
    cursor = expected_spec
    for part in target.metadata_path.split("/")[2:]:
        cursor = cursor[part]
    if step.value is None:
        del cursor[channel][HISTORY_KEY]
    else:
        cursor[channel][HISTORY_KEY] = step.value
    if (
        response.get("spec") != expected_spec
        or response.get("kind") != accepted.get("kind")
        or any(
            response["metadata"].get(key) != accepted["metadata"][key]
            for key in ("uid", "name", "namespace")
        )
        or type(response["metadata"].get("generation")) is not int
        or response["metadata"]["generation"] != accepted["metadata"]["generation"] + 1
        or not isinstance(response["metadata"].get("resourceVersion"), str)
        or not response["metadata"]["resourceVersion"]
        or response["metadata"]["resourceVersion"]
        == accepted["metadata"]["resourceVersion"]
        or response["metadata"].get("labels", {}).get(PHASE_LABEL) != phase
    ):
        raise RuntimeError(
            "Update response does not establish the requested metadata input"
        )
    return response


def run(arguments: Sequence[str], target: MetadataTarget) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workload",
        choices=[
            f"metadata-{c}-{s}"
            for c in ("labels", "annotations")
            for s in ("settled", "overlap")
        ],
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--history-index", type=int)
    parser.add_argument("--repair-timeout", type=float, default=180)
    parser.add_argument("--settle-seconds", type=float, default=15)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args(arguments)
    if not math.isfinite(args.repair_timeout) or args.repair_timeout <= 0:
        parser.error("repair timeout must be finite and positive")
    if not math.isfinite(args.settle_seconds) or args.settle_seconds < 0:
        parser.error("settle seconds must be finite and nonnegative")
    _, channel, schedule = args.workload.split("-")
    catalog = metadata_histories()
    index = (
        args.history_index
        if args.history_index is not None
        else (3 if channel == "labels" else 7)
    )
    if not 0 <= index < len(catalog) or catalog[index].channel != channel:
        parser.error("history index must select the workload's metadata channel")
    selected = catalog[index]
    artifacts = [
        args.output,
        *(
            args.output.with_name(name)
            for name in (
                "metadata-plan.json",
                "metadata-observations.json",
                "semantic-outcome.json",
                "metadata-commands",
            )
        ),
    ]
    if args.plan_only:
        artifacts.append(args.output.with_name("metadata-no-fault.yaml"))
    if any(p.exists() or p.is_symlink() for p in artifacts):
        parser.error("metadata output artifacts already exist; choose a fresh output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": 1,
        "operator": target.operator,
        "generated": [asdict(h) for h in catalog],
        "selected": asdict(selected),
        "schedule": schedule,
        "workers": 10,
        "monitors": ["latest_state", "metadata_cleanup", "history_equivalence"],
        "repair_timeout": args.repair_timeout,
        "settle_seconds": args.settle_seconds,
    }
    args.output.with_name("metadata-plan.json").write_text(
        json.dumps(plan, indent=2) + "\n"
    )
    if args.plan_only:
        write_plan(
            FaultPlan(
                workload=args.workload,
                actions=[],
                coverage_resources=[
                    target.parent_kind + "/default/sieve-metadata-subject"
                ],
            ),
            args.output.with_name("metadata-no-fault.yaml"),
        )
        return 0
    subject_name, reference_name = "sieve-metadata-subject", "sieve-metadata-fresh"
    evidence: Resource = {
        "schema_version": 1,
        "operator": target.operator,
        "status": "running",
        "accepted": [],
        "samples": [],
        "overlap_status": "not_reviewed" if schedule == "overlap" else "not_requested",
    }

    def save() -> None:
        args.output.with_name("metadata-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def wait_for(predicate: Callable[[], bool], description: str) -> None:
        with deadline(args.repair_timeout):
            until = time.monotonic() + args.repair_timeout
            while time.monotonic() < until:
                if predicate():
                    return
                time.sleep(min(0.2, max(0, until - time.monotonic())))
        raise TimeoutError("Timed out: " + description)

    def record(operation: str, response: Resource, role: str) -> None:
        evidence["accepted"].append(
            {
                "role": role,
                "operation": operation,
                "response": response,
                "wall_ns": time.time_ns(),
                "monotonic_ns": time.monotonic_ns(),
            }
        )
        save()

    base = {c: {KEEP_KEY: "retained"} for c in ("labels", "annotations")}
    expected = copy.deepcopy(base)
    stable_complete = False
    stage = "setup_failed"
    with execution(log_dir=args.output.with_name("metadata-commands")):
        try:
            for name in (subject_name, reference_name):
                if (
                    get(target.parent_kind, name) is not None
                    or get("secret", name) is not None
                ):
                    raise RuntimeError(
                        "metadata history requires fresh parent and child names"
                    )
            accepted = target.setup(wait_for, name=subject_name, metadata=base)
            record("initial", accepted, "subject")
            reference: Resource | None = None

            def sample(*, expired: bool = False) -> SemanticVerdict:
                observations = {"subject": target.observe(subject_name)}
                inputs = {"subject": accepted}
                if reference is not None:
                    observations["fresh"] = target.observe(reference_name)
                    inputs["fresh"] = reference
                verdicts: list[SemanticVerdict] = []
                row: Resource = {
                    "wall_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                    "accepted_count": len(evidence["accepted"]),
                }
                for role, observation in observations.items():
                    latest = LatestStateMonitor(
                        accepted=inputs[role], expected_value=target.effective_value
                    ).observe(**asdict(observation))
                    cleanup = MetadataCleanupMonitor(
                        expected=expected,
                        removed={channel: (HISTORY_KEY,)}
                        if HISTORY_KEY not in expected[channel]
                        else {},
                    ).observe(child=observation.child, expired=expired)
                    verdicts.extend((latest, cleanup))
                    row[role] = {
                        **asdict(observation),
                        "latest_state": asdict(latest),
                        "metadata_cleanup": asdict(cleanup),
                    }
                pods = json.loads(
                    kubectl(
                        "get", "pods", "-l", "sievetag=" + target.operator, "-o", "json"
                    )
                )["items"]
                flag = target.worker_argument.split("=", 1)[0]
                configured = bool(pods) and all(
                    len(p["spec"]["containers"]) == 1
                    and [
                        a
                        for a in p["spec"]["containers"][0].get("args", [])
                        if a == flag or a.startswith(flag + "=")
                    ]
                    == [target.worker_argument]
                    for p in pods
                )
                live = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
                epoch = (
                    live[0]["metadata"].get("uid")
                    if len(live) == 1
                    and any(
                        c.get("type") == "Ready" and c.get("status") == "True"
                        for c in live[0].get("status", {}).get("conditions", [])
                    )
                    else None
                )
                if epoch is None:
                    verdicts.append(
                        SemanticVerdict("pending", ("controller_recovery",))
                    )
                if reference is not None and all(
                    v.status == "satisfied" for v in verdicts
                ):
                    fresh, subject = observations["fresh"], observations["subject"]
                    assert fresh.child is not None and fresh.effective_value is not None
                    equivalence = HistoryEquivalenceMonitor(
                        reference_parent=reference,
                        reference_child=fresh.child,
                        reference_value=fresh.effective_value,
                        metadata_keys={c: (KEEP_KEY, HISTORY_KEY) for c in base},
                    ).observe(
                        parent=accepted,
                        child=subject.child,
                        effective_value=subject.effective_value,
                    )
                    verdicts.append(equivalence)
                    row["history_equivalence"] = asdict(equivalence)
                violation = [v for v in verdicts if v.status == "violation"]
                pending = [v for v in verdicts if v.status == "pending"]
                verdict = SemanticVerdict(
                    "violation" if violation else "pending" if pending else "satisfied",
                    tuple(
                        reason for v in (violation or pending) for reason in v.reasons
                    ),
                )
                row.update(
                    verdict=asdict(verdict),
                    controller_pods=pods,
                    controller_configured=configured,
                    controller_uid=epoch,
                )
                evidence["samples"].append(row)
                save()
                if not configured:
                    raise ControllerConfigurationError(
                        "controller must run exactly ten reconciliation workers"
                    )
                if violation:
                    raise SemanticViolation(", ".join(verdict.reasons))
                return verdict

            wait_for(lambda: sample().status == "satisfied", "initial metadata state")
            for index, step in enumerate(selected.steps, 1):
                stage = "input_failed"
                accepted = _patch(target, accepted, channel, step, index)
                record(step.operation, accepted, "subject")
                if step.value is None:
                    del expected[channel][HISTORY_KEY]
                else:
                    expected[channel][HISTORY_KEY] = step.value
                stage = "convergence_failed"
                # Establish that the entry was really managed before removal.
                if schedule == "settled" or index == 1:
                    wait_for(
                        lambda: sample().status == "satisfied",
                        "metadata " + step.operation,
                    )
            wait_for(lambda: sample().status == "satisfied", "final metadata cleanup")
            stage = "reference_failed"
            reference = target.setup(wait_for, name=reference_name, metadata=expected)
            record("fresh", reference, "fresh")
            stage = "convergence_failed"
            window = StabilityWindow(seconds=args.settle_seconds)

            def converged() -> bool:
                verdict = sample()
                row = evidence["samples"][-1]
                return window.observe(
                    verdict, now_ns=row["monotonic_ns"], epoch=row["controller_uid"]
                )

            wait_for(converged, "stable history equivalence")
            stable_complete = True
            evidence["status"] = "passed"
        except Exception as error:
            if (
                isinstance(error, (TimeoutError, ProcessTimeout))
                and stage == "convergence_failed"
                and evidence["samples"]
            ):
                try:
                    sample(expired=True)
                except Exception as final_error:
                    if isinstance(final_error, SemanticViolation):
                        error = final_error
            evidence.update(
                status="configuration_failed"
                if isinstance(error, ControllerConfigurationError)
                else "semantic_violation"
                if isinstance(error, SemanticViolation)
                else stage,
                error=str(error),
            )
            args.output.write_text(str(error) + "\n")
            return 1
        finally:
            save()
            args.output.with_name("semantic-outcome.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checks": [
                            {
                                "name": "metadata_history_and_fresh_state_stable",
                                "passed": stable_complete,
                            }
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0
