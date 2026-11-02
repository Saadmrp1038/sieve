"""Report declared histories, executions, input observations and proven overlap."""

import argparse
import copy
import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from pydantic import TypeAdapter

from sieve_tool.fault_proofs import SemanticOutcome
from sieve_tool.oracle.artifacts import read_artifact, read_outcome
from sieve_tool.testing.child_repair import Resource
from sieve_tool.testing.metadata_cases import validate_case_plan
from sieve_tool.testing.overlap_schedule import verify_scheduled_overlap
from sieve_tool.testing.sequence_coverage import delivery_overlap
from sieve_tool.testing.sequences import (
    HISTORY_KEY,
    KEEP_KEY,
    PHASE_LABEL,
    metadata_histories,
)


def _plan(path: Path) -> Resource:
    plan: Resource = json.loads(path.read_text())
    if not isinstance(plan, dict):
        raise ValueError("metadata plan must be an object")
    if type(plan.get("schema_version")) is int and plan["schema_version"] == 2:
        validate_case_plan(plan)
        return plan
    catalog = json.loads(json.dumps([asdict(h) for h in metadata_histories()]))
    if (
        type(plan.get("schema_version")) is not int
        or plan["schema_version"] != 1
        or plan.get("operator") not in ("external-secrets", "cert-manager")
        or plan.get("schedule") not in ("settled", "overlap")
        or plan.get("generated") != catalog
        or plan.get("selected") not in catalog
        or type(plan.get("workers")) is not int
        or plan["workers"] != 10
    ):
        raise ValueError("invalid bounded metadata catalog or schedule")
    return plan


def _accepted(plan: Resource, observations: Resource) -> list[Resource]:
    rows = [r for r in observations["accepted"] if r["role"] == "subject"]
    steps = plan["selected"]["steps"]
    if [r["operation"] for r in rows] != ["initial", *(s["operation"] for s in steps)]:
        raise ValueError("declared history was not fully accepted")
    responses = [r["response"] for r in rows]
    identity = responses[0]["metadata"]
    expected_kind = {
        "external-secrets": "ExternalSecret",
        "cert-manager": "Certificate",
    }[plan["operator"]]
    versions = set()
    initial_spec = None
    for index, response in enumerate(responses):
        metadata = response["metadata"]
        if (
            response.get("kind") != expected_kind
            or not isinstance(metadata.get("uid"), str)
            or not metadata["uid"]
            or any(metadata.get(k) != identity[k] for k in ("uid", "name", "namespace"))
            or metadata.get("name") != "sieve-metadata-subject"
            or metadata.get("namespace") != "default"
            or type(metadata.get("generation")) is not int
            or metadata["generation"] < 1
            or metadata["generation"] != identity["generation"] + index
            or not isinstance(metadata.get("resourceVersion"), str)
            or not metadata["resourceVersion"]
            or metadata["resourceVersion"] in versions
        ):
            raise ValueError("invalid accepted input identity/version")
        versions.add(metadata["resourceVersion"])
        if (
            index
            and metadata.get("labels", {}).get(PHASE_LABEL)
            != f"metadata-{index}-{steps[index - 1]['operation']}"
        ):
            raise ValueError("accepted phase does not match history")
        spec = copy.deepcopy(response["spec"])
        managed = (
            spec["target"]["template"]["metadata"]
            if plan["operator"] == "external-secrets"
            else spec["secretTemplate"]
        )
        channel = plan["selected"]["channel"]
        expected = steps[index - 1]["value"] if index else None
        if (
            any(
                managed.get(c, {}).get(KEEP_KEY) != "retained"
                for c in ("labels", "annotations")
            )
            or managed[channel].get(HISTORY_KEY) != expected
        ):
            raise ValueError("accepted metadata does not match declared operation")
        managed[channel].pop(HISTORY_KEY, None)
        if index == 0:
            initial_spec = spec
        elif spec != initial_spec:
            raise ValueError("input changed outside the declared metadata entry")
    return responses


def _execution(directory: Path, plan: Resource, row: Resource) -> None:
    row["attempted"] = any(
        (directory / n).exists()
        for n in ("metadata-observations.json", "process-outcome.json")
    )
    if not row["attempted"]:
        return
    observations = json.loads((directory / "metadata-observations.json").read_text())
    if plan["schema_version"] == 2:
        gate = observations.get("delivery_overlap", {})
        initial = gate.get("initial", {}) if isinstance(gate, dict) else {}
        mode = (
            initial.get("mode", "unforced") if isinstance(initial, dict) else "unforced"
        )
        row.update(mode=mode, requires_overlap=mode != "observe")
    responses = _accepted(plan, observations)
    row["accepted"] = True
    process_path = directory / "process-outcome.json"
    process = read_outcome(process_path) if process_path.exists() else None
    semantic_path = directory / "semantic-outcome.json"
    semantic = (
        read_artifact(semantic_path, TypeAdapter(SemanticOutcome))
        if semantic_path.exists()
        else None
    )
    row["reported_semantics"] = (
        "passed"
        if observations.get("status") == "passed"
        and process is not None
        and process.status == "completed"
        and process.returncode == 0
        and semantic is not None
        and all(check.passed for check in semantic.checks)
        and (directory / "workload.log").is_file()
        and "FINISH-SIEVE-TEST" in (directory / "workload.log").read_text()
        else "failed"
    )
    trace = directory / "sieve-server.log"
    if not trace.is_file():
        row["reasons"].append("missing_trace")
        row["valid_evidence"] = False
        return
    coverage = delivery_overlap(trace, accepted=responses)
    row.update(
        overlap_verified=coverage.verified,
        observed_inputs=list(coverage.observed_inputs),
        delivered_inputs=list(coverage.delivered_inputs),
        all_inputs_observed=len(coverage.observed_inputs) == len(responses),
        witnesses=list(coverage.witnesses),
    )
    row["reasons"].extend(coverage.reasons)
    if any(reason != "delivery_overlap_unproven" for reason in coverage.reasons):
        row["valid_evidence"] = False
    if plan["schema_version"] == 2:
        case = validate_case_plan(plan)
        mode = row["mode"]
        row.update(
            mode=mode, requires_overlap=mode != "observe", overlap_verified=False
        )
        if mode == "observe" and "[SIEVE-DELIVERY-OVERLAP]" in trace.read_text():
            row["valid_evidence"] = False
            row["reasons"].append("unexpected_overlap_in_control")
        if mode == "hold":
            schedule = case.plan("hold").delivery_overlap
            assert schedule is not None
            physical = verify_scheduled_overlap(directory, schedule)
            row["overlap_verified"] = physical["verified"]
            row["reasons"].extend(physical["reasons"])
        injection_path = directory / "injection-process-outcome.json"
        injection = read_outcome(injection_path) if injection_path.is_file() else None
        if (
            injection is None
            or injection.status != "completed"
            or injection.returncode != 0
        ):
            row["valid_evidence"] = False
            row["reasons"].append("incomplete_injection_process")
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
            row.update(
                valid_evidence=False, overlap_verified=False, all_inputs_observed=False
            )
            row["reasons"].append("observer_error")


def summarize(directories: Sequence[Path]) -> Resource:
    generated: set[tuple[str, str]] = set()
    selected: set[tuple[str, str, str]] = set()
    cases = []
    for directory in dict.fromkeys(p.resolve() for p in directories):
        path = directory / "metadata-plan.json"
        row: Resource = {
            "directory": str(directory),
            "operator": None,
            "schedule": None,
            "selected": None,
            "attempted": any(
                (directory / n).exists()
                for n in ("metadata-observations.json", "process-outcome.json")
            ),
            "accepted": False,
            "observed_inputs": [],
            "delivered_inputs": [],
            "all_inputs_observed": False,
            "overlap_verified": False,
            "reported_semantics": "unknown",
            "valid_evidence": True,
            "reasons": [],
            "evidence_sha256": {},
        }
        try:
            plan = _plan(path)
            for history in plan["generated"]:
                generated.add((plan["operator"], json.dumps(history, sort_keys=True)))
            selected.add(
                (
                    plan["operator"],
                    plan["schedule"],
                    json.dumps(
                        [plan["selected"], plan.get("placement")], sort_keys=True
                    ),
                )
            )
            row.update(
                operator=plan["operator"],
                schedule=plan["schedule"],
                selected=plan["selected"],
            )
            if plan["schema_version"] == 2:
                row.update(
                    case_id=plan["case_id"],
                    placement=plan["placement"],
                    plan_fingerprint=hashlib.sha256(
                        json.dumps(plan, sort_keys=True).encode()
                    ).hexdigest(),
                )
            _execution(directory, plan, row)
        except (ValueError, KeyError, TypeError, OSError) as error:
            row.update(valid_evidence=False, error=str(error))
            row["reasons"].append("invalid_or_missing_evidence")
        for name in (
            "metadata-plan.json",
            "metadata-observations.json",
            "process-outcome.json",
            "injection-process-outcome.json",
            "semantic-outcome.json",
            "workload.log",
            "sieve-server.log",
            "controller.log",
            "streamed-controller.log",
        ):
            source = directory / name
            if source.is_file():
                row["evidence_sha256"][name] = hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()
        cases.append(row)
    attempted = [r for r in cases if r["attempted"]]
    satisfied = all(
        r["valid_evidence"]
        and r["accepted"]
        and r["reported_semantics"] == "passed"
        and (
            not r.get("requires_overlap", r["schedule"] == "overlap")
            or r["overlap_verified"]
        )
        for r in attempted
    )
    executed_cases = {
        (
            r["operator"],
            r["schedule"],
            json.dumps([r["selected"], r.get("placement")], sort_keys=True),
        )
        for r in attempted
    }
    satisfied = (
        satisfied
        and selected <= executed_cases
        and all(r["valid_evidence"] for r in cases)
    )
    return {
        "schema_version": 1,
        "status": "not_attempted"
        if not attempted
        else "satisfied"
        if satisfied
        else "incomplete",
        "counts": {
            "generated_histories": len(generated),
            "selected_cases": len(selected),
            "attempted_executions": len(attempted),
            "accepted_histories": sum(bool(r["accepted"]) for r in attempted),
            "fully_observed_histories": sum(
                bool(r["all_inputs_observed"]) for r in attempted
            ),
            "verified_overlap_executions": sum(
                bool(r["overlap_verified"]) for r in attempted
            ),
            "required_overlap_executions": sum(
                r.get("requires_overlap", r["schedule"] == "overlap") for r in attempted
            ),
            "required_overlap_verified": sum(
                r.get("requires_overlap", r["schedule"] == "overlap")
                and r["overlap_verified"]
                for r in attempted
            ),
            "reported_semantic_passes": sum(
                r["reported_semantics"] == "passed" for r in attempted
            ),
        },
        "cases": cases,
    }


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.output.exists() or args.output.is_symlink():
        parser.error("report output already exists; choose a fresh output")
    report = summarize(args.case)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
