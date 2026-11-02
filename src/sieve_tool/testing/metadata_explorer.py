"""Freeze bounded history/placement cases; planning never launches workloads."""

import argparse
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from sieve_tool.plans import load_plan, write_plan
from sieve_tool.testing.metadata_cases import OPERATORS, explore, validate_case_plan


def report(manifest_path: Path, directories: Sequence[Path]) -> dict[str, object]:
    from sieve_tool.testing.sequence_report import summarize

    manifest = json.loads(manifest_path.read_text())
    selected = explore(
        operators=tuple(manifest["operators"]), max_cases=manifest["maximum_cases"]
    )
    expected_names = {
        c.case_id + "/" + name
        for c in selected
        for name in ("case.json", "control.yaml", "fault.yaml")
    }
    expected_manifest = {
        "schema_version": 1,
        "maximum_cases": manifest["maximum_cases"],
        "operators": list(manifest["operators"]),
        "generated_cases": len(explore(operators=tuple(manifest["operators"]))),
        "selected_cases": len(selected),
        "workers": 10,
        "automatic_retries": 0,
        "executed_workloads": 0,
        "fault_attempts": 0,
        "cases": [
            {
                "case_id": c.case_id,
                "operator": c.operator,
                "workload": c.workload,
                "case": c.case_id + "/case.json",
                "control": c.case_id + "/control.yaml",
                "fault": c.case_id + "/fault.yaml",
            }
            for c in selected
        ],
        "files": manifest["files"],
    }
    if (
        json.dumps(manifest, sort_keys=True)
        != json.dumps(expected_manifest, sort_keys=True)
        or set(manifest["files"]) != expected_names
    ):
        raise ValueError("invalid frozen exploration manifest")
    root = manifest_path.parent.resolve()
    for name, digest in manifest["files"].items():
        source = root / name
        if (
            source.is_symlink()
            or not source.resolve().is_relative_to(root)
            or hashlib.sha256(source.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("frozen exploration file hash mismatch")
    fingerprints = {}
    for case in selected:
        directory = root / case.case_id
        value = json.loads((directory / "case.json").read_text())
        if (
            validate_case_plan(value) != case
            or load_plan(directory / "control.yaml") != case.plan("observe")
            or load_plan(directory / "fault.yaml") != case.plan("hold")
        ):
            raise ValueError("frozen exploration case/plan mismatch")
        fingerprints[case.case_id] = hashlib.sha256(
            json.dumps(value, sort_keys=True).encode()
        ).hexdigest()
    base = summarize(directories)
    rows = base["cases"]
    for row in rows:
        if (
            row.get("case_id") not in fingerprints
            or row.get("plan_fingerprint") != fingerprints[row["case_id"]]
        ):
            row["valid_evidence"] = False
            row["reasons"].append("case_not_selected_or_frozen_plan_mismatch")
    attempted = [r for r in rows if r["attempted"]]
    valid = [r for r in attempted if r["valid_evidence"] and r["accepted"]]
    controls = {
        r["case_id"]
        for r in valid
        if r.get("mode") == "observe"
        and r["reported_semantics"] == "passed"
        and r["all_inputs_observed"]
        and r["delivered_inputs"] == [1, 2, 3]
    }
    faults = [r for r in valid if r.get("mode") == "hold" and r["overlap_verified"]]
    paired = {r["case_id"] for r in faults} & controls
    operation_pairs = {
        (
            c.operator,
            c.channel,
            c.history.steps[c.hold_after_step - 1].operation,
            c.history.steps[c.release_after_step - 1].operation,
        )
        for c in selected
        if c.case_id in paired
    }
    return {
        "schema_version": 1,
        "status": "not_attempted"
        if not attempted
        else "satisfied"
        if len(paired) == len(selected)
        and all(
            r["valid_evidence"] and r["reported_semantics"] == "passed"
            for r in attempted
        )
        else "incomplete",
        "counts": {
            "generated_cases": len(explore(operators=tuple(manifest["operators"]))),
            "selected_cases": len(selected),
            "attempted_executions": len(attempted),
            "observe_control_executions": sum(
                r.get("mode") == "observe" for r in attempted
            ),
            "fault_attempts": sum(r.get("mode") == "hold" for r in attempted),
            "unclassified_attempts": sum(
                r.get("mode") not in ("hold", "observe") for r in attempted
            ),
            "verified_overlap_executions": len(faults),
            "paired_verified_placements": len(paired),
            "covered_operation_pairs": len(operation_pairs),
            "reported_semantic_passes": sum(
                r["reported_semantics"] == "passed" for r in attempted
            ),
            "reported_semantic_failures": sum(
                r["reported_semantics"] == "failed" for r in attempted
            ),
            "fully_observed_histories": sum(
                bool(r["all_inputs_observed"]) for r in attempted
            ),
        },
        "not_covered_case_ids": sorted(
            c.case_id for c in selected if c.case_id not in paired
        ),
        "cases": rows,
        "evidence_sha256": {
            "exploration.json": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            **manifest["files"],
        },
        "limits": "Artifact-based coverage and reported semantics; native image identity, physical semantics and bug novelty require independent review. Planning launches no workloads.",
    }


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--operator", action="append", choices=OPERATORS)
    plan.add_argument("--max-cases", type=int, default=16)
    plan.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("report")
    review.add_argument("--plan", type=Path, required=True)
    review.add_argument("--case", type=Path, action="append", default=[])
    review.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.command == "report":
        if args.output.exists() or args.output.is_symlink():
            parser.error("report output already exists; choose a fresh output")
        try:
            result = report(args.plan, args.case)
        except (ValueError, KeyError, TypeError, OSError) as error:
            parser.error(str(error))
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        return 0
    try:
        cases = explore(
            operators=tuple(args.operator or OPERATORS), max_cases=args.max_cases
        )
    except ValueError as error:
        parser.error(str(error))
    if args.output.exists() or args.output.is_symlink():
        parser.error("exploration output already exists; choose a fresh output")
    args.output.mkdir(parents=True)
    entries = []
    for case in cases:
        directory = args.output / case.case_id
        directory.mkdir(parents=True)
        (directory / "case.json").write_text(
            json.dumps(case.metadata_plan(), indent=2) + "\n"
        )
        write_plan(case.plan("observe"), directory / "control.yaml")
        write_plan(case.plan("hold"), directory / "fault.yaml")
        entries.append(
            {
                "case_id": case.case_id,
                "operator": case.operator,
                "workload": case.workload,
                "case": case.case_id + "/case.json",
                "control": case.case_id + "/control.yaml",
                "fault": case.case_id + "/fault.yaml",
            }
        )
    manifest = {
        "schema_version": 1,
        "maximum_cases": args.max_cases,
        "operators": list(args.operator or OPERATORS),
        "generated_cases": len(explore(operators=tuple(args.operator or OPERATORS))),
        "selected_cases": len(cases),
        "workers": 10,
        "automatic_retries": 0,
        "executed_workloads": 0,
        "fault_attempts": 0,
        "cases": entries,
        "files": {
            str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(args.output.rglob("*"))
            if p.is_file()
        },
    }
    (args.output / "exploration.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
