"""Fault cases at the harness boundary, independent of a Kubernetes cluster."""

import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import kubernetes
import pytest

from sieve_tool.kubernetes import api
from sieve_tool.paths import repository
from sieve_tool.process import (
    ProcessError,
    ProcessTimeout,
    cleanup_budget,
    execution,
    remaining,
    request_timeout,
    reset_cleanup_budget,
    run,
)
from sieve_tool.runtime import runner, cluster
from sieve_tool.oracle.checker_common import test_run_validation as validate_run


def test_setup_failure_survives_failed_cleanup():
    context = SimpleNamespace(postprocess=False, mode="test", build_oracle=False)
    with (
        patch.object(runner, "prepare_test_plan"),
        patch.object(
            runner, "setup_cluster", side_effect=RuntimeError("original setup failure")
        ),
        patch.object(
            runner,
            "teardown_cluster",
            side_effect=RuntimeError("secondary cleanup failure"),
        ),
    ):
        result = runner.run_test(context)
    assert not result.no_exception
    assert "original setup failure" in result.exception_message
    assert "secondary cleanup failure" in result.exception_message


def test_interrupt_still_tears_down_partial_setup():
    context = SimpleNamespace(postprocess=False, mode="test", build_oracle=False)
    with (
        patch.object(runner, "prepare_test_plan"),
        patch.object(runner, "setup_cluster", side_effect=KeyboardInterrupt),
        patch.object(runner, "teardown_cluster") as teardown,
    ):
        with pytest.raises(KeyboardInterrupt):
            runner.run_test(context)
    teardown.assert_called_once()


@pytest.mark.parametrize("code", [0, 7])
def test_deployment_is_rendered_without_modifying_sources(tmp_path, code):
    checkout = tmp_path / "checkout with spaces"
    source = checkout / "controllers/operator/deploy"
    source.mkdir(parents=True)
    original = "image: ${SIEVE-DR}/operator:${SIEVE-DT}\n"
    (source / "operator.yaml").write_text(original)
    (source / "deploy.sh").write_text(
        "#!/bin/bash\ncat operator.yaml\nexit " + str(code) + "\n"
    )
    output = tmp_path / "result with spaces"
    output.mkdir()
    context = SimpleNamespace(
        use_csi_driver=False,
        controller_config_dir=str(source.parent),
        result_dir=str(output),
        container_registry="registry",
        image_tag="test",
        controller_config=SimpleNamespace(
            controller_deployment_file_path="controllers/operator/deploy/operator.yaml"
        ),
    )
    with repository(checkout), execution(log_dir=tmp_path / "logs"):
        if code:
            with pytest.raises(ProcessError):
                cluster.deploy_controller(context)
        else:
            cluster.deploy_controller(context)
    assert (source / "operator.yaml").read_text() == original
    assert (
        output / "deployment/operator.yaml"
    ).read_text() == "image: registry/operator:test\n"


def test_existing_cluster_is_not_deleted(tmp_path):
    token = cluster._owned.set(False)
    try:
        with patch.object(
            cluster, "run", return_value=SimpleNamespace(stdout="kind\n")
        ) as command:
            with pytest.raises(RuntimeError, match="unowned"):
                cluster.setup_kind_cluster(SimpleNamespace())
            cluster.teardown_cluster()
            assert command.call_count == 1
    finally:
        cluster._owned.reset(token)


def test_nonzero_workload_exit_cannot_be_overridden_by_finish_marker(tmp_path):
    (tmp_path / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (tmp_path / "sieve-server.log").write_text(
        "Sieve test coordinator finishes all actions\n"
    )
    (tmp_path / "process-outcome.json").write_text(
        json.dumps({"status": "failed", "returncode": 7})
    )
    assert validate_run(
        SimpleNamespace(result_dir=str(tmp_path), test_plan_content={"actions": []})
    ) == (True, False)


def test_cleanup_uses_one_shared_budget(tmp_path):
    with execution({"cleanup": 0.3, "terminate": 0.05}, tmp_path):
        reset_cleanup_budget()
        with cleanup_budget():
            time.sleep(0.1)
        with cleanup_budget():
            assert remaining(1) < 0.25
            with pytest.raises(ProcessTimeout):
                run(["/bin/sleep", "5"], operation="blocked teardown", timeout=5)


def test_sdk_request_timeout_has_no_hidden_retries(tmp_path):
    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            time.sleep(0.6)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configuration = kubernetes.client.Configuration(
        host=f"http://127.0.0.1:{server.server_port}"
    )
    configuration.proxy = None
    try:
        with (
            patch.object(
                kubernetes.client.Configuration,
                "get_default_copy",
                return_value=configuration,
            ),
            execution({"request": 0.1}, tmp_path),
        ):
            client = api(kubernetes.client.CoreV1Api)
            assert client.api_client.configuration.retries == 0
            started = time.monotonic()
            with pytest.raises(Exception):
                client.list_namespaced_pod(
                    "default", _request_timeout=request_timeout()
                )
            assert time.monotonic() - started < 0.5
            client.api_client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_incomplete_learning_baseline_is_not_retried_or_canonicalized(tmp_path):
    context = SimpleNamespace(
        postprocess=False, mode="learn", build_oracle=True, result_dir=str(tmp_path)
    )
    (tmp_path / "workload.log").write_text("baseline failed\n")
    (tmp_path / "process-outcome.json").write_text(
        json.dumps({"status": "completed", "returncode": 0})
    )
    with (
        patch.object(runner, "prepare_test_plan"),
        patch.object(runner, "setup_cluster"),
        patch.object(runner, "run_workload") as workload,
        patch.object(runner, "teardown_cluster"),
        patch.object(runner, "collect_diagnostics"),
        patch.object(runner, "post_process") as oracle,
    ):
        result = runner.run_test(context)
    workload.assert_called_once()
    oracle.assert_not_called()
    assert not result.no_exception
    assert "refusing to learn" in result.exception_message


def test_sdk_wall_deadline_stops_a_trickling_response(tmp_path):
    class Trickle(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.03)
            except BrokenPipeError, ConnectionResetError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configuration = kubernetes.client.Configuration(
        host=f"http://127.0.0.1:{server.server_port}"
    )
    configuration.proxy = None
    try:
        with (
            patch.object(
                kubernetes.client.Configuration,
                "get_default_copy",
                return_value=configuration,
            ),
            execution({"request": 0.15}, tmp_path),
        ):
            client = api(kubernetes.client.CoreV1Api)
            started = time.monotonic()
            with pytest.raises(ProcessTimeout):
                client.list_namespaced_pod("default")
            assert time.monotonic() - started < 0.6
            client.api_client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_diagnostics_preserve_status_and_events_before_cluster_deletion(
    tmp_path, monkeypatch
):
    import sys
    from sieve_tool.runtime.workload import collect_diagnostics

    command = tmp_path / "bin/kubectl"
    command.parent.mkdir()
    command.write_text(
        "#!"
        + sys.executable
        + '\nimport json,sys\nprint(json.dumps({"kind":"List","items":[]}) if sys.argv[1]=="get" else "controller log")\n'
    )
    command.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(command.parent) + ":" + __import__("os").environ["PATH"]
    )
    result = tmp_path / "result"
    result.mkdir()
    (result / "apiserver1.log").write_text("original streamed evidence")
    context = SimpleNamespace(
        result_dir=str(result),
        num_apiservers=1,
        mode="vanilla",
        controller="operator",
        common_config=SimpleNamespace(namespace="default"),
    )
    with execution(log_dir=tmp_path / "commands"):
        collect_diagnostics(context)
    assert (result / "apiserver1.log").read_text() == "original streamed evidence"
    assert (
        json.loads((result / "resources-snapshot.json").read_text())["kind"] == "List"
    )
    assert json.loads((result / "events-snapshot.json").read_text())["kind"] == "List"
    assert json.loads((result / "diagnostics.json").read_text())["failures"] == []


def test_explicit_workload_step_budget_preserves_unrelated_defaults(tmp_path):
    import sys
    from sieve_tool.testing.driver import new_built_in_workload, common_config

    original = common_config.workload_command_wait_timeout
    workload = new_built_in_workload().cmd(
        [sys.executable, "-c", "import time;time.sleep(0.15)"], timeout=1
    )
    with execution(log_dir=tmp_path):
        assert workload.work_list[0].run() == (0, "")
    assert workload.work_list[0].timeout == 1
    assert common_config.workload_command_wait_timeout == original
