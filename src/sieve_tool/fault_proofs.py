"""Independent physical fault evidence, alongside unmodified oracle alarms."""

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sieve_tool.acceptance.missed_state import verify_missed_state
from sieve_tool.oracle.artifacts import ArtifactError, read_artifact, read_outcome
from sieve_tool.plans import FaultPlan, PauseControllerAction, RestartControllerAction
from sieve_tool.results import TestResult
from sieve_tool.trace import iter_api_events


class SemanticCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    name: str = Field(min_length=1)
    passed: bool


class SemanticOutcome(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal[1]
    checks: list[SemanticCheck] = Field(min_length=1)


class FaultProof(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    schema_version: Literal[1]
    physically_verified: bool
    reasons: list[str]
    semantic_status: Literal["unknown", "passed", "failed"]
    semantic_checks: list[SemanticCheck]
    raw_alarms: list[str]
    missed_state: dict[str, object] | None
    evidence_sha256: dict[str, str]
    workload_completed: bool


def read_fault_proof(path: Path) -> FaultProof:
    proof = read_artifact(path, TypeAdapter(FaultProof))
    for name, expected in proof.evidence_sha256.items():
        if Path(name).name != name:
            raise ArtifactError(f"{path}: invalid evidence path {name}")
        source = path.parent / name
        if (
            not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise ArtifactError(f"{path}: evidence hash mismatch for {name}")
    return proof


def audit_fault(
    directory: Path, plan: FaultPlan, result: TestResult, *, namespace: str = "default"
) -> dict[str, object]:
    reasons: list[str] = []
    actions = plan.actions or []
    restarts = [a for a in actions if isinstance(a, RestartControllerAction)]
    missed = None
    if any(isinstance(a, PauseControllerAction) for a in actions):
        missed = verify_missed_state(directory, plan)
        reasons.extend(missed.reasons)
    elif len(restarts) != 1 or len(actions) != 1:
        reasons.append("physical proof for this action sequence is unavailable")
    else:
        labels = {a.controller_label for a in restarts}
        deleted: list[str] = []
        ready: set[str] = set()
        first_seen: dict[str, int] = {}
        deletion_started: dict[str, int] = {}
        path = directory / "apiserver1.log"
        if path.exists():
            for position, event in enumerate(iter_api_events(path)):
                if event.rtype != "pod" or event.namespace != namespace:
                    continue
                metadata = event.get_metadata_value("labels") or {}
                if metadata.get("sievetag") not in labels:
                    continue
                uid = event.get_metadata_value("uid")
                if not isinstance(uid, str):
                    continue
                first_seen.setdefault(uid, position)
                if (
                    event.get_metadata_value("deletionTimestamp")
                    or event.etype == "DELETED"
                ):
                    deletion_started.setdefault(uid, position)
                if event.etype == "DELETED":
                    deleted.append(uid)
                obj = event.obj_map or {}
                status = obj.get("status", obj.get("Status", {}))
                if (
                    deleted
                    and uid not in deleted
                    and any(first_seen[uid] > deletion_started[old] for old in deleted)
                    and not event.get_metadata_value("deletionTimestamp")
                    and isinstance(status, dict)
                    and any(
                        c.get("type", c.get("Type")) == "Ready"
                        and c.get("status", c.get("Status")) == "True"
                        for c in (
                            status.get("conditions", status.get("Conditions", [])) or []
                        )
                    )
                ):
                    ready.add(uid)
        if len(set(deleted)) < len(restarts):
            reasons.append("matching old controller Pod API deletion is unproven")
        if len(ready) < len(restarts):
            reasons.append("distinct nonterminating Ready replacement is unproven")
        server_path = directory / "sieve-server.log"
        server = server_path.read_text() if server_path.exists() else ""
        if server.count("controller pod gets deleted") != len(restarts):
            reasons.append("matching coordinator restart actions are unproven")
    outcome_path = directory / "injection-process-outcome.json"
    outcome = read_outcome(outcome_path) if outcome_path.exists() else None
    if outcome is None or outcome.status != "completed" or outcome.returncode != 0:
        reasons.append("injection process did not complete successfully")
    if not result.injection_completed or not result.no_exception:
        reasons.append("raw injection outcome is incomplete or has a harness exception")
    semantic_path = directory / "semantic-outcome.json"
    semantic = (
        read_artifact(semantic_path, TypeAdapter(SemanticOutcome))
        if semantic_path.exists()
        else None
    )
    process_path = directory / "process-outcome.json"
    process = read_outcome(process_path) if process_path.exists() else None
    workload_completed = bool(
        result.workload_completed
        and process is not None
        and process.status == "completed"
        and process.returncode == 0
    )
    source_names = (
        "apiserver1.log",
        "sieve-server.log",
        "injection-process-outcome.json",
        "process-outcome.json",
        "workload.log",
        "workload-updates.json",
        "semantic-outcome.json",
    )
    return {
        "schema_version": 1,
        "physically_verified": not reasons,
        "reasons": reasons,
        "semantic_status": "unknown"
        if semantic is None
        else "passed"
        if workload_completed and all(check.passed for check in semantic.checks)
        else "failed",
        "semantic_checks": []
        if semantic is None
        else [c.model_dump() for c in semantic.checks],
        "raw_alarms": result.common_errors
        + result.end_state_errors
        + result.history_errors,
        "missed_state": asdict(missed) if missed is not None else None,
        "workload_completed": workload_completed,
        "evidence_sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in source_names
            if (directory / name).is_file()
        },
    }
