from sieve_tool.plans import load_plan
from sieve_tool.results import TestResult

"""Execute workloads and apply analysis/oracles; no CLI or import-time I/O."""

import errno
import glob
import json
import os
import shutil
import socket
import time
import traceback
from dataclasses import replace
from pathlib import Path

import yaml

from sieve_tool.analysis import analyze
from sieve_tool.common.common import (
    TestContext,
    first_pass_learn_result_dir,
    rmtree_if_exists,
    sieve_modes,
)
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.oracle.checker_common import print_error_and_debugging_info
from sieve_tool.oracle.oracle import (
    check,
    create_differential_oracles,
    save_controller_related_object_list,
    save_history,
    save_state,
)
from sieve_tool.paths import asset_path, repository_root
from sieve_tool.process import cleanup_budget, execution, reset_cleanup_budget

from .cluster import (
    cluster_lease,
    generate_testing_cluster_config,
    setup_cluster,
    teardown_cluster,
)
from .workload import collect_diagnostics, run_workload


def save_run_result(
    test_context: TestContext,
    test_result: TestResult,
    start_time,
):
    """
    Save the testing result into a json file for later debugging.
    The test result json contains the test plan, the errors detected by the oracles and so on.
    """
    if test_context.mode != sieve_modes.TEST:
        return

    if test_result is None:
        raise AssertionError("test result should not be None")

    result_map = {
        test_context.controller: {
            test_context.test_workload: {
                test_context.mode: {
                    test_context.original_test_plan: {
                        "duration": time.time() - start_time,
                        **test_result.to_dict(),
                        "test_plan_content": open(
                            test_context.original_test_plan
                        ).read()
                        if test_context.mode == sieve_modes.TEST
                        else "",
                        "host": socket.gethostname(),
                        "execution": json.loads(
                            (
                                Path(test_context.result_dir) / "process-outcome.json"
                            ).read_text()
                        )
                        if (
                            Path(test_context.result_dir) / "process-outcome.json"
                        ).exists()
                        else None,
                    }
                }
            },
        }
    }

    # Write the raw test result beside the per-plan artifact tree.
    result_filename = "{}/{}-{}-{}.json".format(
        test_context.result_root_dir,
        test_context.controller,
        test_context.test_workload,
        os.path.basename(test_context.original_test_plan),
    )
    if not os.path.exists(os.path.dirname(result_filename)):
        try:
            os.makedirs(os.path.dirname(result_filename))
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise

    with open(result_filename, "w") as test_result_json:
        json.dump(
            result_map,
            test_result_json,
            indent=4,
        )


def save_history_and_end_state(test_context: TestContext):
    """
    Generate three files:
    (1) a list of controller related objects (e.g., the controller pod) which will not be considered when applying differential oracles
    because the injected fault (e.g., controller crash) directly affects their states.
    (2) the entire history, including all the creation, deletion and update events, during the test workload,
    which is used for generating and applying differential oracles.
    (3) the end state after the test workload, which is used for generating and applying differential oracles.
    """
    save_controller_related_object_list(test_context)
    save_history(test_context)
    save_state(test_context)


def post_process(
    test_context: TestContext,
) -> TestResult:
    """
    Postprocess the collected logs and results.
    For learn mode, it will generate the test plan and differential oracles.
    For test mode, it will apply the differential oracles and report any detected errors.
    """
    if test_context.mode == sieve_modes.LEARN:
        if test_context.build_oracle:
            create_differential_oracles(test_context)
        analyze.generate_test_plans_from_learn_run(test_context)
        return None
    elif test_context.mode == sieve_modes.VANILLA:
        return None
    else:
        assert test_context.mode == sieve_modes.TEST
        test_result = check(test_context)
        print_error_and_debugging_info(test_context, test_result)
        return test_result


def save_previous_learn_results(test_context: TestContext):
    """
    Move the learn result to a differen folder.
    This should be only called in learn mode with build_oracle enabled.
    To build the differential oracles, we need to run the same workload twice
    to eliminate the non-determinism from the end state and state updates.
    """
    assert test_context.mode == sieve_modes.LEARN and test_context.build_oracle
    learn_res_dir = test_context.result_dir
    learn_prev_res_dir = first_pass_learn_result_dir(test_context.result_dir)
    assert os.path.isdir(learn_res_dir), (
        "{} should exist after first pass of learn run".format(learn_res_dir)
    )
    print(
        "Moving the first pass learn result from {} to {}...".format(
            learn_res_dir, learn_prev_res_dir
        )
    )
    rmtree_if_exists(learn_prev_res_dir)
    shutil.move(learn_res_dir, learn_prev_res_dir)


def prepare_test_plan(test_context: TestContext):
    """
    Prepare the test plan.
    The test plan for test mode details what fault to inject and when to inject.
    The plans for learn and vanilla mode are rather simple:
    for learn mode, it only needs to contain the list of CRD to help filter out the irrevelant events;
    for vanilla mode it is empty.
    """
    if os.path.exists(test_context.result_dir):
        raise FileExistsError(
            f"Result directory already exists: {test_context.result_dir}; choose a fresh --output"
        )
    os.makedirs(test_context.result_dir)
    print("Sieve result dir: {}".format(test_context.result_dir))
    # Prepare the test plan for different modes
    if test_context.mode == sieve_modes.LEARN:
        # The plan for learn mode just contains the CRD list to fliter out events irrelevant to the controller
        print("Config for learn mode: {}".format(test_context.test_plan))
        create_plan_for_learn_mode(test_context)
    elif test_context.mode == sieve_modes.VANILLA:
        # The plan for vanilla mode is basically empty
        print("Config for vanilla mode: {}".format(test_context.test_plan))
        create_plan_for_vanilla_mode(test_context)
    else:
        # The test plan details what fault to inject and where to inject
        # and is generated by learn mode
        assert test_context.mode == sieve_modes.TEST
        print("Test plan: {}".format(test_context.test_plan))
        create_plan_for_test_mode(test_context)


def run_test(test_context: TestContext) -> TestResult:
    cluster_started = False
    try:
        if test_context.postprocess:
            return post_process(test_context)
        passes = (
            2
            if test_context.mode == sieve_modes.LEARN and test_context.build_oracle
            else 1
        )
        for index in range(passes):
            reset_cleanup_budget()
            prepare_test_plan(test_context)
            cluster_started = True
            setup_cluster(test_context)
            run_workload(test_context)
            if test_context.mode in (sieve_modes.LEARN, sieve_modes.VANILLA):
                directory = Path(test_context.result_dir)
                marker = directory / "workload.log"
                outcome_file = directory / "process-outcome.json"
                outcome = (
                    json.loads(outcome_file.read_text())
                    if outcome_file.exists()
                    else {}
                )
                if not (
                    marker.exists()
                    and marker.read_text().strip() == "FINISH-SIEVE-TEST"
                    and outcome.get("status") == "completed"
                    and outcome.get("returncode") == 0
                ):
                    raise RuntimeError(
                        "Baseline workload did not complete; refusing to learn from an incomplete run"
                    )
            with cleanup_budget():
                teardown_cluster()
            cluster_started = False
            save_history_and_end_state(test_context)
            if index + 1 < passes:
                save_previous_learn_results(test_context)
        return post_process(test_context)
    except BaseException as primary:
        error = traceback.format_exc()
        if cluster_started:
            with cleanup_budget():
                try:
                    directory = (
                        Path(test_context.result_dir)
                        if hasattr(test_context, "result_dir")
                        else None
                    )
                    if (
                        directory is not None
                        and not (directory / "diagnostics.json").exists()
                    ):
                        collect_diagnostics(test_context)
                except Exception as secondary:
                    primary.add_note(f"Diagnostic collection also failed: {secondary}")
                try:
                    teardown_cluster()
                except Exception as secondary:
                    primary.add_note(f"Cluster cleanup also failed: {secondary}")
        if not isinstance(primary, Exception):
            raise
        print(error)
        return TestResult(
            injection_completed=False,
            workload_completed=False,
            common_errors=[],
            end_state_errors=[],
            history_errors=[],
            no_exception=False,
            exception_message=error + "\n".join(getattr(primary, "__notes__", [])),
        )


def create_plan_for_test_mode(test_context: TestContext):
    test_plan_content = load_plan(test_context.original_test_plan).to_dict()
    # TODO: we should probably just add the annotatedReconcileStackFrame when generating the test plan.
    test_plan_content["annotatedReconcileStackFrame"] = [
        i for i in test_context.controller_config.annotated_reconcile_functions.values()
    ]
    yaml.dump(test_plan_content, open(test_context.test_plan, "w"), sort_keys=False)


def create_plan_for_learn_mode(test_context: TestContext):
    crd_list = test_context.controller_config.custom_resource_definitions
    learn_plan_content = {}
    # NOTE: we use the CRD list to focus on recording the events relevant to the controller during learn run
    # Here we assume all the relevant events are related to the CR objects or their owned objects
    # TODO: support customized defintion of "relevant events"
    learn_plan_content["crdList"] = crd_list
    # NOTE: rateLimiterEnabled is deprecated, will remove later
    learn_plan_content["rateLimiterEnabled"] = False
    learn_plan_content["rateLimiterInterval"] = 3
    learn_plan_content["annotatedReconcileStackFrame"] = [
        i for i in test_context.controller_config.annotated_reconcile_functions.values()
    ]
    yaml.dump(learn_plan_content, open(test_context.test_plan, "w"), sort_keys=False)


def create_plan_for_vanilla_mode(test_context: TestContext):
    vanilla_plan_content = {}
    yaml.dump(vanilla_plan_content, open(test_context.test_plan, "w"), sort_keys=False)


def get_test_workload_from_test_plan(test_plan_file):
    return load_plan(test_plan_file).workload


def run(
    controller_config_dir,
    test_workload,
    result_root_dir,
    mode,
    test_plan,
    container_registry,
    postprocess,
    build_oracle,
    *,
    oracle_root=None,
    common_config=None,
):
    """
    Prepare the test context based on the input options and the configurations
    and start to run the test.
    """
    controller_config_dir = str(asset_path(controller_config_dir).resolve())
    result_root_dir = str(Path(result_root_dir).expanduser().resolve())
    common_config = common_config or get_common_config()
    controller_config = load_controller_config(controller_config_dir)
    if test_workload is None:
        assert mode == sieve_modes.TEST
        test_workload = get_test_workload_from_test_plan(test_plan)
        print("Get test workload {} from test plan".format(test_workload))
    num_apiservers, num_workers, use_csi_driver = generate_testing_cluster_config(
        mode, controller_config, test_plan, test_workload
    )
    oracle_dir = os.path.join(
        oracle_root or str(repository_root() / "runs/oracles"),
        controller_config.controller_name,
        test_workload,
    )
    assert (
        os.path.isdir(oracle_dir)
        or (mode == sieve_modes.LEARN and build_oracle)
        or mode == sieve_modes.VANILLA
    ), (
        "The oracle dir: {} must exist unless (1) you are running vanilla mode or (2) build_oracle is enabled".format(
            oracle_dir
        )
    )
    os.makedirs(oracle_dir, exist_ok=True)
    if mode == sieve_modes.TEST:
        result_dir = os.path.join(
            result_root_dir,
            controller_config.controller_name,
            test_workload,
            mode,
            os.path.splitext(os.path.basename(test_plan))[0],
        )
    else:
        result_dir = os.path.join(
            result_root_dir,
            controller_config.controller_name,
            test_workload,
            mode,
        )

    image_tag = mode
    test_plan_to_run = os.path.join(result_dir, os.path.basename(test_plan))
    # Prepare the context for testing the controller
    test_context = TestContext(
        controller=controller_config.controller_name,
        controller_config_dir=controller_config_dir,
        test_workload=test_workload,
        mode=mode,
        postprocess=postprocess,
        build_oracle=build_oracle,
        original_test_plan=test_plan,
        test_plan=test_plan_to_run,
        result_root_dir=result_root_dir,
        result_dir=result_dir,
        oracle_dir=oracle_dir,
        container_registry=container_registry,
        image_tag=image_tag,
        num_apiservers=num_apiservers,
        num_workers=num_workers,
        use_csi_driver=use_csi_driver,
        common_config=common_config,
        controller_config=controller_config,
    )
    overrides = controller_config.test_setting.get(test_workload, {}).get(
        "process_timeouts", {}
    )
    test_context.common_config = replace(
        common_config, process_timeouts={**common_config.process_timeouts, **overrides}
    )
    with execution(
        test_context.common_config.process_timeouts, Path(result_dir) / "commands"
    ):
        if postprocess:
            test_result = run_test(test_context)
        else:
            with cluster_lease():
                test_result = run_test(test_context)
    return test_result, test_context


def run_batch(
    controller,
    test_workload,
    dir,
    mode,
    test_plan_folder,
    docker,
    postprocess,
    build_oracle,
    *,
    oracle_root=None,
    common_config=None,
):
    """
    Run multiple test plans in the test_plan_folder in a batch.
    """
    assert mode == sieve_modes.TEST, "batch mode only allowed in test mode for now"
    assert os.path.isdir(test_plan_folder), "{} should be a folder".format(
        test_plan_folder
    )
    test_plans = glob.glob(os.path.join(test_plan_folder, "*.yaml"))
    for test_plan in test_plans:
        load_plan(test_plan)
    print("Test plans to run:")
    print("\n".join(test_plans))
    results = []
    for test_plan in sorted(test_plans):
        start_time = time.time()
        test_result, test_context = run(
            controller,
            test_workload,
            dir,
            mode,
            test_plan,
            docker,
            postprocess,
            build_oracle,
            oracle_root=oracle_root,
            common_config=common_config,
        )
        results.append((test_result, test_context))
        save_run_result(
            test_context,
            test_result,
            start_time,
        )
        outcome = Path(test_context.result_dir) / "process-outcome.json"
        timed_out = (
            outcome.exists()
            and json.loads(outcome.read_text()).get("status") == "timed_out"
        )
        if (
            not test_result.no_exception
            or not test_result.workload_completed
            or timed_out
        ):
            print(
                "Stopping batch after an execution failure; retained plans have not been retried"
            )
            break

    return results
