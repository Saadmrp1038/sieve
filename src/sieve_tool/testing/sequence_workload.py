"""Execute one generated operation history and retain semantic observations."""

import argparse
import json
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from sieve_tool.plans import write_plan
from sieve_tool.process import deadline, execution
from sieve_tool.testing.child_repair import Resource, Wait, get, kubectl
from sieve_tool.testing.semantic import (
    LatestStateMonitor,
    SemanticVerdict,
    StabilityWindow,
)
from sieve_tool.testing.sequences import PHASE_LABEL, bounded_sequences, sequence_plans


class SemanticViolation(RuntimeError):
    """An observed safety property failed, even if later reconciliation heals."""


class ControllerConfigurationError(RuntimeError):
    """The run does not have the requested native concurrency configuration."""


@dataclass(frozen=True)
class SequenceState:
    input_value: Any
    effective_value: str


@dataclass(frozen=True)
class SequenceObservation:
    parent: Resource | None
    child: Resource | None
    effective_value: str | None
    ready_generation: int | None


class SequenceTarget(Protocol):
    operator: str
    parent_kind: str
    name: str
    field_path: str
    worker_argument: str
    states: dict[str, SequenceState]

    def setup(self, wait: Wait) -> Resource: ...

    def observe(self) -> SequenceObservation: ...


def patch_input(
    target: SequenceTarget, accepted: Resource, phase: str, state: str
) -> Resource:
    current = get(target.parent_kind, target.name)
    assert current is not None
    labels = {**current["metadata"].get("labels", {}), PHASE_LABEL: phase}
    response: Resource = json.loads(
        kubectl(
            "patch",
            target.parent_kind,
            target.name,
            "--type=json",
            "-p",
            json.dumps(
                [
                    {
                        "op": "test",
                        "path": "/metadata/uid",
                        "value": accepted["metadata"]["uid"],
                    },
                    {"op": "add", "path": "/metadata/labels", "value": labels},
                    {
                        "op": "replace",
                        "path": target.field_path,
                        "value": target.states[state].input_value,
                    },
                ]
            ),
            "-o",
            "json",
        )
    )
    value: Any = response
    for component in target.field_path.strip("/").split("/"):
        component = component.replace("~1", "/").replace("~0", "~")
        value = value[int(component)] if isinstance(value, list) else value[component]
    metadata = response["metadata"]
    if (
        metadata.get("uid") != accepted["metadata"]["uid"]
        or not metadata.get("resourceVersion")
        or metadata.get("labels", {}).get(PHASE_LABEL) != phase
        or type(metadata.get("generation")) is not int
        or metadata["generation"] <= accepted["metadata"]["generation"]
        or value != target.states[state].input_value
    ):
        raise RuntimeError("Update response does not establish the requested input")
    return response


def run(arguments: Sequence[str], target: SequenceTarget) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["bounded-sequences"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-sequences", type=int, default=16)
    parser.add_argument("--sequence-index", type=int, default=8)
    parser.add_argument("--update-interval", type=float, default=0.25)
    parser.add_argument("--repair-timeout", type=float, default=180)
    parser.add_argument("--settle-seconds", type=float, default=15)
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Write the bounded catalog and matched plans without contacting Kubernetes",
    )
    args = parser.parse_args(arguments)
    try:
        catalog = bounded_sequences(
            initial="a",
            states=tuple(target.states),
            max_steps=args.max_steps,
            max_sequences=args.max_sequences,
        )
    except ValueError as error:
        parser.error(str(error))
    if not 0 <= args.sequence_index < len(catalog):
        parser.error("sequence index is outside the bounded catalog")
    for name in ("repair_timeout", "update_interval", "settle_seconds"):
        value = getattr(args, name)
        if (
            not math.isfinite(value)
            or value < 0
            or name == "repair_timeout"
            and value == 0
        ):
            parser.error(
                name
                + " must be finite and nonnegative (repair timeout must be positive)"
            )
    artifacts = [
        args.output,
        *(
            args.output.with_name(n)
            for n in (
                "sequence-plan.json",
                "sequence-observations.json",
                "semantic-outcome.json",
                "sequence-commands",
                "sequence-no-fault.yaml",
                "sequence-restart.yaml",
            )
        ),
    ]
    if any(p.exists() or p.is_symlink() for p in artifacts):
        parser.error("sequence output artifacts already exist; choose a fresh output")
    selected = catalog[args.sequence_index]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": 1,
        "operator": target.operator,
        "initial": "a",
        "generated": catalog,
        "selected": selected,
        "max_steps": args.max_steps,
        "max_sequences": args.max_sequences,
        "state_values": {k: asdict(v) for k, v in target.states.items()},
        "repair_timeout": args.repair_timeout,
        "update_interval": args.update_interval,
        "settle_seconds": args.settle_seconds,
    }
    args.output.with_name("sequence-plan.json").write_text(
        json.dumps(plan, indent=2) + "\n"
    )
    for name, fault_plan in sequence_plans(
        controller_label=target.operator,
        parent_key=f"{target.parent_kind}/default/{target.name}",
        selected=selected,
    ).items():
        write_plan(fault_plan, args.output.with_name(name))
    if args.plan_only:
        return 0
    evidence: Resource = {
        "schema_version": 1,
        "operator": target.operator,
        "status": "running",
        "accepted": [],
        "samples": [],
    }

    def save() -> None:
        args.output.with_name("sequence-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def wait_for(predicate: Callable[[], bool], description: str) -> None:
        with deadline(args.repair_timeout):
            until = time.monotonic() + args.repair_timeout
            while time.monotonic() < until:
                if predicate():
                    return
                time.sleep(min(0.2, max(0, until - time.monotonic())))
        raise RuntimeError("Timed out: " + description)

    failure = "setup_failed"
    stable_complete = False
    with execution(log_dir=args.output.parent / "sequence-commands"):
        try:
            accepted = target.setup(wait_for)
            evidence["accepted"].append(
                {
                    "state": "a",
                    "response": accepted,
                    "wall_ns": time.time_ns(),
                    "monotonic_ns": time.monotonic_ns(),
                }
            )

            def sample(state: str) -> bool:
                observation = target.observe()
                verdict = LatestStateMonitor(
                    accepted=accepted,
                    expected_value=target.states[state].effective_value,
                ).observe(**asdict(observation))
                pods = json.loads(
                    kubectl(
                        "get", "pods", "-l", "sievetag=" + target.operator, "-o", "json"
                    )
                )["items"]
                configured = True
                flag = target.worker_argument.split("=", 1)[0]
                for pod in pods:
                    containers = pod["spec"]["containers"]
                    configured = (
                        configured
                        and len(containers) == 1
                        and [
                            a
                            for a in containers[0].get("args", [])
                            if a == flag or a.startswith(flag + "=")
                        ]
                        == [target.worker_argument]
                    )
                live = [p for p in pods if not p["metadata"].get("deletionTimestamp")]
                controller_uid = None
                if len(live) == 1 and any(
                    c.get("type") == "Ready" and c.get("status") == "True"
                    for c in live[0].get("status", {}).get("conditions", [])
                ):
                    controller_uid = live[0]["metadata"].get("uid")
                if controller_uid is None and verdict.status != "violation":
                    verdict = SemanticVerdict("pending", ("controller_recovery",))
                evidence["samples"].append(
                    {
                        "expected_state": state,
                        "accepted_generation": accepted["metadata"]["generation"],
                        "wall_ns": time.time_ns(),
                        "monotonic_ns": time.monotonic_ns(),
                        **asdict(observation),
                        "verdict": asdict(verdict),
                        "controller_pods": pods,
                        "controller_configured": configured if pods else None,
                        "controller_uid": controller_uid,
                    }
                )
                save()
                if not configured:
                    raise ControllerConfigurationError(
                        "controller must run exactly ten reconciliation workers"
                    )
                if verdict.status == "violation":
                    raise SemanticViolation(", ".join(verdict.reasons))
                return verdict.status == "satisfied"

            wait_for(lambda: sample("a"), "initial state")
            for index, state in enumerate(selected, 1):
                failure = "input_failed"
                accepted = patch_input(
                    target, accepted, f"sequence-{index}-{state}", state
                )
                evidence["accepted"].append(
                    {
                        "state": state,
                        "response": accepted,
                        "wall_ns": time.time_ns(),
                        "monotonic_ns": time.monotonic_ns(),
                    }
                )
                save()
                failure = "convergence_failed"
                if index == 1:
                    wait_for(partial(sample, state), "first sequence update")
                else:
                    sample(state)
                if index != len(selected):
                    time.sleep(args.update_interval)
            stable = StabilityWindow(seconds=args.settle_seconds)

            def converged() -> bool:
                sample(selected[-1])
                verdict = SemanticVerdict(**evidence["samples"][-1]["verdict"])
                latest = evidence["samples"][-1]
                return stable.observe(
                    verdict,
                    now_ns=latest["monotonic_ns"],
                    epoch=latest["controller_uid"],
                )

            wait_for(converged, "stable latest state")
            stable_complete = True
            evidence["status"] = "passed"
        except Exception as error:
            status = (
                "configuration_failed"
                if isinstance(error, ControllerConfigurationError)
                else "semantic_violation"
                if isinstance(error, SemanticViolation)
                else failure
            )
            evidence.update(status=status, error=str(error))
            args.output.write_text(str(error) + "\n")
            return 1
        finally:
            save()
            observed = evidence["samples"]
            checks = {
                "declared_sequence_accepted": len(evidence["accepted"])
                == len(selected) + 1,
                "parent_identity": bool(observed)
                and all(
                    "parent_identity" not in s["verdict"]["reasons"] for s in observed
                ),
                "accepted_input": bool(observed)
                and all(
                    "accepted_input" not in s["verdict"]["reasons"] for s in observed
                ),
                "child_ownership": bool(observed)
                and all(
                    "child_ownership" not in s["verdict"]["reasons"] for s in observed
                ),
                "ready_means_latest": bool(observed)
                and all("ready_value" not in s["verdict"]["reasons"] for s in observed),
                "latest_state_stable": stable_complete,
                "ten_workers_in_observed_pods": bool(observed)
                and all(s["controller_configured"] is not False for s in observed),
            }
            args.output.with_name("semantic-outcome.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checks": [
                            {"name": c, "passed": passed}
                            for c, passed in checks.items()
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0
