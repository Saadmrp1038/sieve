from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import signal
import kubernetes

from sieve_tool.common.common import *
from .cluster import start_controller, stop_coordinator


def run_workload(
    test_context: TestContext,
):
    """
    Deploy the controller and run the provided testing workload.
    """

    cprint("Deploying controller...", bcolors.OKGREEN)
    start_controller(test_context)
    ok("Controller deployed")

    # If there are multiple contains in the controller pod then we need to find the correct one.
    select_container_from_pod = (
        " -c {} ".format(test_context.controller_config.container_name)
        if test_context.controller_config.container_name is not None
        else ""
    )

    kubernetes.config.load_kube_config()
    pod_name = (
        kubernetes.client.CoreV1Api()
        .list_namespaced_pod(
            test_context.common_config.namespace,
            watch=False,
            label_selector="sievetag=" + test_context.controller,
        )
        .items[0]
        .metadata.name
    )
    # Stream the controller log to streamed-controller.log for debugging purpose.
    # If the controller crashes due to some panic, Sieve will report that by checking
    # the streamed log.
    streamed_log_file = open(
        os.path.join(test_context.result_dir, "streamed-controller.log"), "w+"
    )
    streaming = subprocess.Popen(
        "kubectl logs {} {} -f".format(pod_name, select_container_from_pod),
        stdout=streamed_log_file,
        stderr=streamed_log_file,
        shell=True,
        preexec_fn=os.setsid,
    )

    # Stream the apiserver log.
    # The apiserver log should contain the state changes (i.e., creation, deletion and update events)
    # during the test workload and the log content will be later used for
    # (1) generating differential oracles for learn mode and
    # (2) detecting errors (e.g., inconsistency in end states) for test mode.
    # TODO: Storing everything in a log is a bit hacky,
    # and it is better to save all the recorded state changes in a database during runtime,
    # if that does not hurt the performance too much.
    # Another alternative is to just watch all the objects using the local kubectl.
    streamed_api_server_log_file = open(
        os.path.join(test_context.result_dir, "apiserver1.log"), "w+"
    )
    streaming_api_server = subprocess.Popen(
        "kubectl logs kube-apiserver-kind-control-plane -n kube-system -f",
        stdout=streamed_api_server_log_file,
        stderr=streamed_api_server_log_file,
        shell=True,
        preexec_fn=os.setsid,
    )

    cprint("Running test workload...", bcolors.OKGREEN)
    test_command = "{} {} {}".format(
        test_context.controller_config.test_command,
        test_context.test_workload,
        os.path.join(test_context.result_dir, "workload.log"),
    )
    settings = Path(test_context.result_dir).resolve() / "harness-config.json"
    settings.write_text(json.dumps(asdict(test_context.common_config), indent=2) + "\n")
    process = subprocess.Popen(
        test_command,
        shell=True,
        env={**os.environ, "SIEVE_HARNESS_CONFIG": str(settings)},
    )
    process.wait()

    pod_name = (
        kubernetes.client.CoreV1Api()
        .list_namespaced_pod(
            test_context.common_config.namespace,
            watch=False,
            label_selector="sievetag=" + test_context.controller,
        )
        .items[0]
        .metadata.name
    )

    # Also save the log of other apiservers (for multi-apiserver set up) for debugging purpose.
    for i in range(1, test_context.num_apiservers):
        apiserver_name = "kube-apiserver-kind-control-plane" + (
            "" if i == 0 else str(i + 1)
        )
        apiserver_log = "apiserver{}.log".format(str(i + 1))
        os_system(
            "kubectl logs {} -n kube-system > {}/{}".format(
                apiserver_name, test_context.result_dir, apiserver_log
            )
        )

    # Save the Sieve server log.
    # In learn mode, the Sieve server log contains the collected controller events
    # which will be used to generate test plans.
    if test_context.mode != sieve_modes.VANILLA:
        os_system(
            "docker cp kind-control-plane:/sieve/coordinator/sieve-server.log {}/sieve-server.log".format(
                test_context.result_dir
            )
        )

    # Save the controller log as well mainly for debugging purpose.
    os_system(
        "kubectl logs --request-timeout=30s {} {} > {}/controller.log".format(
            pod_name, select_container_from_pod, test_context.result_dir
        )
    )
    # Stop streaming controller log.
    try:
        os.killpg(streaming.pid, signal.SIGTERM)
    except Exception as e:
        print("Unable to kill controller log streaming process: ", e)
    streamed_log_file.close()
    # Stop streaming apiserver log.
    os.killpg(streaming_api_server.pid, signal.SIGTERM)
    streamed_api_server_log_file.close()

    if test_context.mode != sieve_modes.VANILLA:
        stop_coordinator()
