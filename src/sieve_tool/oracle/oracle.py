"""Load selected evidence, invoke pure oracles and save learned references."""

import json
from dataclasses import asdict
from pathlib import Path

from sieve_tool.common.common import TestContext, bcolors, cprint, sieve_modes
from sieve_tool.oracle.artifacts import (
    read_digest,
    read_family,
    read_history,
    read_outcome,
    read_state,
)
from sieve_tool.oracle.customized_safety_checker import customized_safety_checker_suites
from sieve_tool.oracle.learning import (
    canonicalize_controller_related_list,
    canonicalize_history_digest,
    generate_controller_related_list,
    second_pass_learn_trim,
)
from sieve_tool.oracle.liveness_checker import (
    compare_states,
    generate_state,
    generate_state_mask,
)
from sieve_tool.oracle.policy import ComparisonPolicy
from sieve_tool.oracle.safety_checker import (
    apply_safety_checker,
    compare_history_digests,
    generate_history,
    generate_history_digest,
)
from sieve_tool.oracle.validation import (
    controller_panic_checker,
    validate_execution,
    workload_error_checker,
)
from sieve_tool.plans import FaultPlan, PauseControllerAction, parse_plan
from sieve_tool.results import TestResult
from sieve_tool.trace import iter_api_events


def _save(directory: str | Path, name: str, value: object) -> None:
    with (Path(directory) / name).open("w") as stream:
        json.dump(value, stream, indent=4, sort_keys=True)


def save_history(test_context: TestContext) -> None:
    cprint("Generating state update summary...", bcolors.OKGREEN)
    path = Path(test_context.result_dir) / "apiserver1.log"
    _save(
        test_context.result_dir, "history.json", generate_history(iter_api_events(path))
    )
    digest = generate_history_digest(
        iter_api_events(path),
        test_context.common_config.state_update_summary_check_event_list,
    )
    _save(test_context.result_dir, "event.json", digest)


def save_state(test_context: TestContext) -> None:
    cprint("Generating end state...", bcolors.OKGREEN)
    state = generate_state(
        iter_api_events(Path(test_context.result_dir) / "apiserver1.log")
    )
    _save(test_context.result_dir, "state.json", state)


def save_controller_related_object_list(test_context: TestContext) -> None:
    cprint("Generating controller family list...", bcolors.OKGREEN)
    family = generate_controller_related_list(
        iter_api_events(Path(test_context.result_dir) / "apiserver1.log")
    )
    _save(test_context.result_dir, "controller_family.json", family)


def create_differential_oracles(test_context: TestContext) -> None:
    if not test_context.common_config.update_oracle_file_enabled:
        return
    assert test_context.mode == sieve_modes.LEARN and test_context.build_oracle
    second = Path(test_context.result_dir)
    first = second.with_name("learn_prev")
    cprint("Generating canonicalized state update summary...", bcolors.OKGREEN)
    digest = canonicalize_history_digest(
        read_digest(first / "event.json"), read_digest(second / "event.json")
    )
    _save(test_context.oracle_dir, "event.json", digest)
    cprint("Generating canonicalized end state...", bcolors.OKGREEN)
    state = second_pass_learn_trim(
        read_state(first / "state.json"), read_state(second / "state.json")
    )
    _save(test_context.oracle_dir, "state.json", state)
    cprint(
        "Generating canonicalized state mask (for generating test plans)...",
        bcolors.OKGREEN,
    )
    _save(test_context.oracle_dir, "mask.json", generate_state_mask(state))
    cprint("Copying controller family to the oracle dir...", bcolors.OKGREEN)
    family = canonicalize_controller_related_list(
        read_family(first / "controller_family.json"),
        read_family(second / "controller_family.json"),
    )
    _save(test_context.oracle_dir, "controller_family.json", family)


def validate_run(
    test_context: TestContext,
    plan: FaultPlan | None = None,
    *,
    require_missed_state: bool = False,
) -> tuple[bool, bool]:
    directory = Path(test_context.result_dir)
    if plan is None and test_context.test_plan_content is not None:
        plan = parse_plan(test_context.test_plan_content)
    outcome = directory / "process-outcome.json"
    injected, completed = validate_execution(
        (directory / "sieve-server.log").read_text(),
        (directory / "workload.log").read_text(),
        plan,
        read_outcome(outcome) if outcome.is_file() else None,
    )
    if require_missed_state:
        from sieve_tool.acceptance.missed_state import verify_missed_state

        if plan is None:
            raise ValueError("missed-state validation requires a plan")
        proof = verify_missed_state(directory, plan)
        _save(directory, "missed-state-proof.json", asdict(proof))
        injected = injected and proof.verified
    return injected, completed


def check(test_context: TestContext) -> TestResult:
    plan = parse_plan(test_context.test_plan_content)
    independent = test_context.controller_config.test_setting.get(
        test_context.test_workload, {}
    ).get("independent_updates", False)
    read_pause = any(
        isinstance(a, PauseControllerAction) and a.pause_at == "beforeControllerRead"
        for a in (plan.actions or [])
    )
    injection_completed, workload_completed = validate_run(
        test_context, plan, require_missed_state=independent and read_pause
    )
    common = test_context.common_config
    policy = ComparisonPolicy(
        test_context.test_workload, common, test_context.controller_config
    )
    directory = Path(test_context.result_dir)
    reference = Path(test_context.oracle_dir)
    common_errors: list[str] = []
    history_errors: list[str] = []
    end_state_errors: list[str] = []
    if common.controller_exception_check_enabled:
        common_errors.extend(
            controller_panic_checker(
                (directory / "streamed-controller.log").read_text()
            )
        )
    if common.workload_error_check_enabled:
        common_errors.extend(
            workload_error_checker(
                (directory / "workload.log").read_text(),
                "pauseController" in test_context.action_types,
            )
        )
    if common.state_update_summary_check_enabled:
        # Preserve the existing exemption for schedules that intentionally miss a read.
        first_action = plan.actions[0] if plan.actions is not None else None
        if not (
            isinstance(first_action, PauseControllerAction)
            and first_action.pause_at == "beforeControllerRead"
        ):
            history_errors.extend(
                compare_history_digests(
                    read_digest(reference / "event.json"),
                    read_digest(directory / "event.json"),
                    read_family(directory / "controller_family.json"),
                    policy,
                )
            )
    if customized_safety_checker_suites:
        history = read_history(directory / "history.json")
        for suite in customized_safety_checker_suites:
            history_errors.extend(
                apply_safety_checker(
                    history,
                    suite.resource_keys,
                    suite.checker_name,
                    suite.checker_function,
                )
            )
    if common.end_state_check_enabled and not (
        test_context.use_csi_driver_for_ref and not test_context.use_csi_driver
    ):
        end_state_errors.extend(
            compare_states(
                read_state(reference / "state.json"),
                read_state(directory / "state.json"),
                policy,
                read_family(reference / "controller_family.json"),
                read_family(directory / "controller_family.json"),
            )
        )
    return TestResult(
        injection_completed,
        workload_completed,
        common_errors,
        end_state_errors,
        history_errors,
        True,
        "",
    )
