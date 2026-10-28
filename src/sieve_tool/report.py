"""Inspect saved results without starting a cluster."""

import json
from pathlib import Path

from sieve_tool.fault_proofs import read_fault_proof


def proof_report(directory: Path) -> int:
    proofs = [read_fault_proof(p) for p in sorted(directory.rglob("fault-proof.json"))]
    document = {
        "schema_version": 1,
        "attempted": len(proofs),
        "physically_verified": sum(p.physically_verified for p in proofs),
        "semantic_passed": sum(p.semantic_status == "passed" for p in proofs),
        "semantic_failed": sum(p.semantic_status == "failed" for p in proofs),
        "semantic_unknown": sum(p.semantic_status == "unknown" for p in proofs),
        "raw_alarm_count": sum(len(p.raw_alarms) for p in proofs),
        "cases": [p.model_dump() for p in proofs],
    }
    print(json.dumps(document, indent=2))
    return (
        0
        if proofs
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
