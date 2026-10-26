"""Execution evidence checks, independent of files and clusters."""

from collections import Counter
from io import StringIO

from sieve_tool.oracle.artifacts import WorkloadOutcome
from sieve_tool.oracle.checker_common import generate_alarm
from sieve_tool.plans import FaultPlan, PauseControllerAction


def validate_execution(
    server_output: str,
    workload_output: str,
    plan: FaultPlan | None,
    outcome: WorkloadOutcome | None = None,
) -> tuple[bool, bool]:
    injected = "Sieve test coordinator finishes all actions" in server_output
    required = Counter(
        (action.pause_at, action.pause_scope)
        for action in (plan.actions or [] if plan is not None else [])
        if isinstance(action, PauseControllerAction)
        and action.pause_at in ("beforeControllerRead", "afterControllerRead")
    )
    for (pause_at, scope), count in required.items():
        if server_output.count(f": Pause controller at {pause_at} {scope}\n") < count:
            injected = False
        if (
            server_output.count(f": End pause controller at {pause_at} {scope}\n")
            < count
        ):
            injected = False
    completed = "FINISH-SIEVE-TEST" in workload_output
    if outcome is not None:
        completed = (
            completed and outcome.status == "completed" and outcome.returncode == 0
        )
    return injected, completed


def controller_panic_checker(output: str) -> list[str]:
    messages = []
    for line in StringIO(output):
        if "Observed a panic" in line:
            panic = line[line.find("Observed a panic") :]
            messages.append(generate_alarm("Exception from controller:", panic.strip()))
    return sorted(messages)


def workload_error_checker(output: str, pause_controller: bool) -> list[str]:
    messages = []
    for line in StringIO(output):
        if line.startswith("FINISH-SIEVE-TEST"):
            break
        if pause_controller and line.startswith("Conditional wait timeout"):
            continue
        messages.append(generate_alarm("Error:", line.strip()))
    return sorted(messages)
