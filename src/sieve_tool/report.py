"""Inspect saved results without starting a cluster."""

import json
from pathlib import Path


def report(directory):
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
