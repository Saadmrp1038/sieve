"""Compare generated plans to the preserved historical corpus."""

import hashlib
import json
from pathlib import Path

from sieve_tool.events import ControllerWrite
from sieve_tool.plans import (
    ObjectCondition,
    PauseControllerAction,
    ReconnectControllerAction,
    ResumeControllerAction,
    StateMatchCondition,
    UpdateCondition,
    load_plan,
)
from sieve_tool.testing.updates import read_updates
from sieve_tool.trace import read_trace


def diagnose(plans: Path, trace: Path) -> int:
    updates_path = trace.parent / "workload-updates.json"
    independent = read_updates(updates_path) if updates_path.is_file() else None
    writes = [
        r.event
        for r in read_trace(trace).records
        if isinstance(r.event, ControllerWrite) and r.event.error == "NoError"
    ]
    rows: list[dict[str, object]] = []
    stale = 0
    for path in sorted(plans.rglob("*.yaml")):
        plan = load_plan(path)
        if independent is not None and independent.workload != plan.workload:
            raise ValueError(
                "independent update evidence belongs to a different workload"
            )
        diagnostics: list[dict[str, object]] = []
        for index, action in enumerate(plan.actions or []):
            stale += int(isinstance(action, ReconnectControllerAction))
            for definition in action.trigger.definitions:
                condition, observation = (
                    definition.condition,
                    definition.observation_point,
                )
                if (
                    not isinstance(condition, (ObjectCondition, UpdateCondition))
                    or observation is None
                    or observation.when != "afterControllerWrite"
                    or isinstance(condition, UpdateCondition)
                    and (
                        condition.prev_state_diff is not None
                        or condition.cur_state_diff is not None
                    )
                ):
                    diagnostics.append(
                        {
                            "code": "predicate_requires_review",
                            "action": index,
                            "trigger": definition.trigger_name,
                        }
                    )
                    continue
                operations = {
                    "onObjectCreate": {"Create"},
                    "onObjectDelete": {"Delete"},
                    "onObjectUpdate": {
                        "Update",
                        "Patch",
                        "StatusUpdate",
                        "StatusPatch",
                    },
                }
                wanted = operations.get(condition.condition_type)
                if wanted is None:
                    diagnostics.append(
                        {
                            "code": "predicate_requires_review",
                            "action": index,
                            "trigger": definition.trigger_name,
                        }
                    )
                    continue
                count = sum(
                    w.etype in wanted
                    and w.reconcile_fun == observation.by
                    and (
                        w.key.startswith(condition.resource_key[:-1])
                        if condition.resource_key_pattern
                        else w.key == condition.resource_key
                    )
                    for w in writes
                )
                if count < condition.occurrence:
                    diagnostics.append(
                        {
                            "code": "occurrence_not_observed",
                            "action": index,
                            "trigger": definition.trigger_name,
                            "required": condition.occurrence,
                            "observed": count,
                        }
                    )
        pauses = [
            a
            for a in plan.actions or []
            if isinstance(a, PauseControllerAction)
            and a.pause_at == "beforeControllerRead"
        ]
        for resume in (
            a for a in plan.actions or [] if isinstance(a, ResumeControllerAction)
        ):
            for definition in resume.trigger.definitions:
                condition = definition.condition
                if not isinstance(
                    condition, (ObjectCondition, UpdateCondition, StateMatchCondition)
                ):
                    continue
                producers = {
                    w.reconcile_fun for w in writes if w.key == condition.resource_key
                }
                if (
                    producers
                    and not (
                        independent is not None
                        and independent.resource_key == condition.resource_key
                    )
                    and any(
                        p.pause_scope == resume.pause_scope
                        and (p.pause_by is None or producers == {p.pause_by})
                        for p in pauses
                    )
                ):
                    diagnostics.append(
                        {
                            "code": "resume_depends_on_paused_consumer",
                            "trigger": definition.trigger_name,
                            "resource_key": condition.resource_key,
                            "note": "All observed controller producers use the paused consumer. Review independent producers and concurrent workers before replay.",
                        }
                    )
        rows.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "diagnostics": diagnostics,
            }
        )
    report = {
        "schema_version": 1,
        "generated": len(rows),
        "stale_state_plans": stale,
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "interpretation": "Baseline support is diagnostic evidence, not a guarantee of replay reachability or an effective injection.",
        "plans": rows,
    }
    print(json.dumps(report, indent=2))
    return 0 if rows and all(not row["diagnostics"] for row in rows) else 1


def check(root: Path) -> int:
    generated = {
        path.read_text(): path
        for path in (root / "runs/learn").glob("**/learn/*/*.yaml")
    }
    missing = 0
    for path in sorted((root / "fixtures/reproductions").glob("*.yaml")):
        if "indirect" in path.name:
            continue
        match = generated.get(path.read_text())
        if match is None:
            print(f"{path}: no matching learned plan")
            missing += 1
        else:
            print(f"{path} <= {match}")
    return 1 if missing else 0
