"""Workload completion must not tear down an unfinished fault action."""

import functools
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import kubernetes
import pytest

from sieve_tool.common.config import get_common_config
from sieve_tool.oracle.oracle import validate_run
from sieve_tool.paths import repository
from sieve_tool.process import execution
from sieve_tool.runtime.workload import run_workload


@pytest.fixture
def cluster_boundary(tmp_path, monkeypatch):
    # Exercise real SDK/command boundaries without a local Docker daemon.
    class Cluster(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/version":
                value = {"ApiVersion": "1.44"}
            elif "/containers/" in self.path:
                value = {
                    "NetworkSettings": {"Ports": {"6443/tcp": [{"HostPort": "6443"}]}}
                }
            elif self.path.startswith("/api/v1/namespaces/default/pods"):
                value = {
                    "apiVersion": "v1",
                    "kind": "PodList",
                    "items": [
                        {
                            "metadata": {"name": "operator"},
                            "status": {
                                "phase": "Running",
                                "conditions": [{"type": "Ready", "status": "True"}],
                            },
                        }
                    ],
                }
            else:
                self.send_error(404)
                return
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Cluster)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "test",
                "clusters": [{"name": "test", "cluster": {"server": endpoint}}],
                "contexts": [
                    {"name": "test", "context": {"cluster": "test", "user": "test"}}
                ],
                "users": [{"name": "test", "user": {}}],
            }
        )
    )
    monkeypatch.setattr(
        kubernetes.config,
        "load_kube_config",
        functools.partial(
            kubernetes.config.load_kube_config, config_file=str(kubeconfig)
        ),
    )
    monkeypatch.setenv("DOCKER_HOST", endpoint.replace("http:", "tcp:"))
    docker_config = tmp_path / "docker-config"
    docker_config.mkdir()
    (docker_config / "config.json").write_text("{}")
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    node = tmp_path / "node"
    node.mkdir()
    monkeypatch.setenv("SIEVE_TEST_NODE", str(node))
    commands = {
        "docker": """import os,shutil,sys
from pathlib import Path
node=Path(os.environ["SIEVE_TEST_NODE"])
args=sys.argv[1:]
if args[0]=="cp":
    shutil.copyfile(node/"sieve-server.log",args[-1])
elif args[:3]==["exec","kind-control-plane","bash"]:
    os.execv("/bin/bash",["bash",*[(arg.replace("/sieve/coordinator",str(node))) for arg in args[3:]]])
else:
    raise SystemExit("unexpected Docker command")
""",
        "kubectl": """import json,sys,time
if "-f" in sys.argv:time.sleep(30)
elif "get" in sys.argv:print(json.dumps({"items":[]}))
""",
    }
    for name, source in commands.items():
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "output"
    output.mkdir()
    deploy = tmp_path / "controllers/operator/deploy"
    deploy.mkdir(parents=True)
    (deploy / "operator.yaml").write_text("image: ${SIEVE-DR}/operator:${SIEVE-DT}\n")
    (deploy / "deploy.sh").write_text("#!/bin/bash\nexit 0\n")
    workload = tmp_path / "workload.py"
    workload.write_text(
        'import pathlib,sys\npathlib.Path(sys.argv[2]).write_text("FINISH-SIEVE-TEST\\n")\n'
    )
    context = SimpleNamespace(
        controller="operator",
        mode="test",
        result_dir=str(output),
        common_config=get_common_config(),
        use_csi_driver=False,
        num_apiservers=1,
        controller_config_dir=str(deploy.parent),
        container_registry="test",
        image_tag="test",
        controller_config=SimpleNamespace(
            container_name=None,
            custom_resource_definitions=[],
            controller_deployment_file_path=str(deploy / "operator.yaml"),
            test_command=[sys.executable, str(workload)],
        ),
        test_workload="repair",
        test_plan_content={"workload": "repair", "actions": None},
    )
    try:
        yield context, node, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fault_action_can_finish_after_the_workload(cluster_boundary):
    context, node, root = cluster_boundary
    marker = Path(context.result_dir) / "workload.log"
    (node / "sieve-server.log").write_text("Restart action started\n")
    coordinator = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import pathlib,sys,time
marker,log=map(pathlib.Path,sys.argv[1:])
while not marker.exists():time.sleep(.01)
time.sleep(2)
with log.open("a") as stream:stream.write("Sieve test coordinator finishes all actions\\n")
time.sleep(30)
""",
            str(marker),
            str(node / "sieve-server.log"),
        ]
    )
    (node / "coordinator.pid").write_text(str(coordinator.pid) + "\n")
    try:
        with (
            repository(root),
            execution({"cluster": 4, "terminate": 0.05}, root / "commands"),
        ):
            run_workload(context)
        assert validate_run(context) == (True, True)
    finally:
        coordinator.terminate()
        coordinator.wait(timeout=2)


def test_failed_application_still_records_the_completed_fault_process(cluster_boundary):
    context, node, root = cluster_boundary
    (root / "workload.py").write_text(
        'import pathlib,sys\npathlib.Path(sys.argv[2]).write_text("application invariant failed\\n")\nraise SystemExit(7)\n'
    )
    (node / "sieve-server.log").write_text(
        "Sieve test coordinator finishes all actions\n"
    )
    coordinator = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    (node / "coordinator.pid").write_text(str(coordinator.pid) + "\n")
    try:
        with (
            repository(root),
            execution({"cluster": 2, "terminate": 0.05}, root / "commands"),
        ):
            run_workload(context)
        directory = Path(context.result_dir)
        outcome = json.loads((directory / "injection-process-outcome.json").read_text())
        assert outcome["status"] == "completed"
        assert (
            json.loads((directory / "process-outcome.json").read_text())["returncode"]
            == 7
        )
        assert validate_run(context) == (True, False)
    finally:
        coordinator.terminate()
        coordinator.wait(timeout=2)


def test_failed_application_does_not_wait_for_an_untriggered_schedule(cluster_boundary):
    context, node, root = cluster_boundary
    (root / "workload.py").write_text("raise SystemExit(7)\n")
    (node / "sieve-server.log").write_text("no action triggered\n")
    coordinator = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    (node / "coordinator.pid").write_text(str(coordinator.pid) + "\n")
    try:
        with (
            repository(root),
            execution({"cluster": 3, "terminate": 0.05}, root / "commands"),
        ):
            run_workload(context)
        outcome = json.loads(
            (Path(context.result_dir) / "injection-process-outcome.json").read_text()
        )
        assert outcome["status"] == "incomplete"
        assert outcome["duration_seconds"] < 2
    finally:
        coordinator.terminate()
        coordinator.wait(timeout=2)


@pytest.mark.parametrize("coordinator_state", ["running", "exited"])
def test_unfinished_schedule_is_bounded_and_keeps_the_workload_result(
    cluster_boundary, coordinator_state
):
    context, node, root = cluster_boundary
    marker = Path(context.result_dir) / "workload.log"
    (node / "sieve-server.log").write_text("Restart action started\n")
    coordinator = subprocess.Popen(
        [
            sys.executable,
            "-c",
            """
import pathlib,sys,time
while not pathlib.Path(sys.argv[1]).exists():time.sleep(.01)
if sys.argv[2]=="running":time.sleep(30)
""",
            str(marker),
            coordinator_state,
        ]
    )
    (node / "coordinator.pid").write_text(str(coordinator.pid) + "\n")
    try:
        with (
            repository(root),
            execution({"cluster": 2, "terminate": 0.05}, root / "commands"),
        ):
            run_workload(context)
        assert validate_run(context) == (False, True)
        outcome = json.loads(
            (Path(context.result_dir) / "injection-process-outcome.json").read_text()
        )
        assert outcome["status"] == (
            "timed_out" if coordinator_state == "running" else "failed"
        )
    finally:
        coordinator.terminate()
        coordinator.wait(timeout=2)
