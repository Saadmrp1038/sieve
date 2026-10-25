from sieve_tool.kubernetes import api as kubernetes_api, load_config

"""Run workloads and own their log followers through success and failure."""
from contextlib import ExitStack
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import kubernetes
from sieve_tool.common.common import sieve_modes
from sieve_tool.paths import repository_root
from sieve_tool.process import (
    cleanup_budget,
    remaining,
    ManagedProcess,
    ProcessTimeout,
    deadline,
    limit,
    request_timeout,
    run,
)
from .cluster import start_controller, stop_coordinator


def collect_diagnostics(context):
    if not hasattr(context, "result_dir"):
        return
    directory = Path(context.result_dir)
    if not directory.exists():
        return
    failures = []
    with cleanup_budget(), deadline(min(60, remaining(limit("cleanup")) / 2)):
        commands = []
        for i in range(context.num_apiservers):
            output = directory / f"apiserver{i + 1}.log"
            if output.exists():
                continue
            name = "kube-apiserver-kind-control-plane" + (str(i + 1) if i else "")
            commands.append(
                (
                    ["kubectl", "logs", name, "-n", "kube-system"],
                    output,
                    "collect API-server log",
                )
            )
        if context.mode != sieve_modes.VANILLA:
            commands.append(
                (
                    [
                        "docker",
                        "cp",
                        "kind-control-plane:/sieve/coordinator/sieve-server.log",
                        directory / "sieve-server.log",
                    ],
                    None,
                    "collect coordinator log",
                )
            )
        commands.append(
            (
                [
                    "kubectl",
                    "logs",
                    "-l",
                    "sievetag=" + context.controller,
                    "--tail=-1",
                ],
                directory / "controller.log",
                "collect controller log",
            )
        )
        for argv, output, operation in commands:
            try:
                result = run(
                    argv,
                    operation=operation,
                    category="request",
                    output=output,
                    check=False,
                )
                if result.returncode:
                    failures.append(
                        f"{operation}: exit {result.returncode}; logs: {result.log}"
                    )
            except Exception as error:
                failures.append(str(error))
    (directory / "diagnostics.json").write_text(
        json.dumps({"failures": failures}, indent=2) + "\n"
    )


def run_workload(context):
    start_controller(context)
    directory = Path(context.result_dir)
    load_config()
    pods = (
        kubernetes_api(kubernetes.client.CoreV1Api)
        .list_namespaced_pod(
            context.common_config.namespace,
            watch=False,
            label_selector="sievetag=" + context.controller,
            _request_timeout=request_timeout(),
        )
        .items
    )
    if not pods:
        raise RuntimeError("Controller disappeared before workload startup")
    container = (
        ["-c", context.controller_config.container_name]
        if context.controller_config.container_name
        else []
    )
    outcome = {"status": "starting", "returncode": None}
    try:
        followers = ExitStack()
        try:
            followers.enter_context(
                ManagedProcess(
                    ["kubectl", "logs", pods[0].metadata.name, *container, "-f"],
                    operation="follow controller log",
                    category="workload",
                    output=directory / "streamed-controller.log",
                )
            )
            followers.enter_context(
                ManagedProcess(
                    [
                        "kubectl",
                        "logs",
                        "kube-apiserver-kind-control-plane",
                        "-n",
                        "kube-system",
                        "-f",
                    ],
                    operation="follow API-server log",
                    category="workload",
                    output=directory / "apiserver1.log",
                )
            )
            settings = directory / "harness-config.json"
            settings.write_text(
                json.dumps(asdict(context.common_config), indent=2) + "\n"
            )
            command = list(context.controller_config.test_command)
            if command[0] in ("python", "python3"):
                command[0] = sys.executable
            try:
                result = run(
                    [*command, context.test_workload, directory / "workload.log"],
                    operation="execute workload",
                    category="workload",
                    cwd=repository_root(),
                    env={"SIEVE_HARNESS_CONFIG": str(settings)},
                    output=directory / "workload-process.log",
                    check=False,
                )
                outcome = {
                    "status": "completed" if result.returncode == 0 else "failed",
                    "returncode": result.returncode,
                    "duration_seconds": result.duration,
                    "log": os.path.relpath(result.log, directory),
                }
            except ProcessTimeout as error:
                outcome = {
                    "status": "timed_out",
                    "returncode": None,
                    "error": str(error),
                }
            except BaseException as error:
                outcome = {
                    "status": "cancelled"
                    if isinstance(error, KeyboardInterrupt)
                    else "failed",
                    "returncode": None,
                    "error": str(error),
                }
                raise
            finally:
                (directory / "process-outcome.json").write_text(
                    json.dumps(outcome, indent=2) + "\n"
                )
        finally:
            with cleanup_budget():
                collect_diagnostics(context)
                followers.close()
    finally:
        if context.mode != sieve_modes.VANILLA:
            with cleanup_budget():
                try:
                    stop_coordinator()
                except Exception as error:
                    print(f"Coordinator cleanup failed: {error}", flush=True)
