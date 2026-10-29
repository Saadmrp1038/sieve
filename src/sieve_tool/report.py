"""Inspect saved results without starting a cluster."""

import json
from dataclasses import dataclass
from pathlib import Path

from sieve_tool.fault_proofs import read_fault_proof
from sieve_tool.plans import load_plan


@dataclass
class _SavedAttempt:
    source: Path
    directory: Path
    alarms: list[str]


def _saved_attempts(directory: Path) -> list[_SavedAttempt]:
    attempts = []
    for path in directory.rglob("*.json"):
        try:
            document = json.loads(path.read_text())
        except json.JSONDecodeError:
            # Archived source fixtures can contain JSON streams. Saved YAML
            # verdict files are selected artifacts and must still fail closed.
            if path.name.endswith((".yaml.json", ".yml.json")):
                raise
            continue
        if not isinstance(document, dict):
            continue
        for controller, workloads in document.items():
            if not isinstance(workloads, dict):
                continue
            for workload, modes in workloads.items():
                if not isinstance(modes, dict) or not isinstance(
                    modes.get("test"), dict
                ):
                    continue
                for plan, result in modes["test"].items():
                    if not isinstance(result, dict) or not {
                        "injection_completed",
                        "test_plan_content",
                    }.issubset(result):
                        continue
                    if any(
                        not name or name in (".", "..") or Path(name).name != name
                        for name in (controller, workload)
                    ):
                        raise ValueError(f"{path}: invalid saved attempt identity")
                    alarms = result.get("detected_errors")
                    if not isinstance(alarms, list) or not all(
                        isinstance(alarm, str) for alarm in alarms
                    ):
                        raise ValueError(f"{path}: invalid saved raw alarms")
                    attempts.append(
                        _SavedAttempt(
                            path,
                            path.parent
                            / controller
                            / workload
                            / "test"
                            / Path(plan).stem,
                            alarms,
                        )
                    )
    return attempts


def proof_report(directory: Path, plans: Path | None = None) -> int:
    proof_paths = sorted(directory.rglob("fault-proof.json"))
    proofs = [read_fault_proof(p) for p in proof_paths]
    proof_directories = {p.parent for p in proof_paths}
    # Fault auditing can fail after the raw verdict is saved. Discover those
    # attempts independently, including timeouts without an injection outcome.
    saved_attempts = _saved_attempts(directory)
    attempts = (
        proof_directories
        | {p.parent for p in directory.rglob("injection-process-outcome.json")}
        | {a.directory for a in saved_attempts if a.directory.is_dir()}
    )
    missing = sorted(str(p / "fault-proof.json") for p in attempts - proof_directories)
    available = attempts - {a.directory for a in saved_attempts}
    unproven = []
    for attempt in saved_attempts:
        if attempt.directory in proof_directories:
            continue
        # Public callers can save a single case into a nonstandard directory.
        # Associate only an unambiguous case below its raw result's own root.
        candidates = [p for p in available if attempt.source.parent in p.parents]
        if not attempt.directory.is_dir() and len(candidates) == 1:
            available.remove(candidates[0])
            attempt.directory = candidates[0]
        if attempt.directory not in proof_directories:
            unproven.append(attempt)
    unlocated = sum(a.directory not in attempts for a in unproven)
    generated = None
    if plans is not None:
        if not plans.is_dir():
            raise ValueError(f"Plan corpus must be an existing directory: {plans}")
        generated = 0
        for path in plans.rglob("*.yaml"):
            load_plan(path)
            generated += 1
    document = {
        "schema_version": 1,
        "attempted": len(attempts) + unlocated,
        "generated": generated,
        "triggered": sum(bool(p.triggered_actions) for p in proofs),
        "workloads_completed": sum(p.workload_completed for p in proofs),
        "physically_verified": sum(p.physically_verified for p in proofs),
        "semantic_passed": sum(p.semantic_status == "passed" for p in proofs),
        "semantic_failed": sum(p.semantic_status == "failed" for p in proofs),
        "semantic_unknown": sum(p.semantic_status == "unknown" for p in proofs)
        + len(missing)
        + unlocated,
        "missing_proofs": missing,
        "unlocated_attempts": unlocated,
        "raw_alarm_count": sum(len(p.raw_alarms) for p in proofs)
        + sum(len(a.alarms) for a in unproven),
        "unproven_raw_results": [
            {"result": str(a.source), "raw_alarms": a.alarms} for a in unproven
        ],
        "cases": [p.model_dump() for p in proofs],
    }
    print(json.dumps(document, indent=2))
    return (
        0
        if proofs
        and not missing
        and not unlocated
        and all(p.physically_verified and p.semantic_status == "passed" for p in proofs)
        else 1
    )


def report(directory: str | Path) -> int:
    found = False
    for path in sorted(Path(directory).rglob("*.json")):
        document = json.loads(path.read_text())
        if not isinstance(document, dict):
            continue
        for controller in document.values():
            if not isinstance(controller, dict):
                continue
            for workload in controller.values():
                if not isinstance(workload, dict):
                    continue
                for mode in workload.values():
                    if not isinstance(mode, dict):
                        continue
                    for result in mode.values():
                        if (
                            not isinstance(result, dict)
                            or "injection_completed" not in result
                        ):
                            continue
                        if result["number_errors"] or not all(
                            result[key]
                            for key in (
                                "injection_completed",
                                "workload_completed",
                                "no_exception",
                            )
                        ):
                            print(path)
                            found = True
    return 1 if found else 0
