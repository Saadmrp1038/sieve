from sieve_tool.kubernetes import api as kubernetes_api
from sieve_tool.kubernetes import load_config

"""Create and tear down the cluster owned by this run, within finite budgets."""
import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

import docker
import kubernetes
import yaml

from sieve_tool.common.common import sieve_modes
from sieve_tool.paths import asset_path, cache_path, repository_root
from sieve_tool.process import (
    ProcessError,
    bounded,
    limit,
    remaining,
    request_timeout,
    run,
    wall_timeout,
)

_owned = ContextVar("sieve_cluster_owned", default=False)


@contextmanager
def cluster_lease():
    path = Path(tempfile.gettempdir()) / f"sieve-kind-{os.getuid()}.lock"
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "Another Sieve run owns the kind cluster on this host"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def create_configmap(test_plan):
    path = Path(test_plan)
    content = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "sieve-testing-global-config"},
        "data": {"sieveTestPlan": json.dumps(yaml.safe_load(path.read_text()))},
    }
    target = path.with_name(path.stem + "-configmap.yaml")
    target.write_text(yaml.safe_dump(content, sort_keys=False))
    return str(target)


def create_kind_config(num_apiservers, num_workers):
    directory = cache_path("kind")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"kind-{num_apiservers}a-{num_workers}w.yaml"
    target.write_text(
        yaml.safe_dump(
            {
                "kind": "Cluster",
                "apiVersion": "kind.x-k8s.io/v1alpha4",
                "nodes": [{"role": "control-plane"} for _ in range(num_apiservers)]
                + [{"role": "worker"} for _ in range(num_workers)],
            },
            sort_keys=False,
        )
    )
    return str(target)


def redirect_workers(test_context):
    for i in range(test_context.num_workers):
        worker = "kind-worker" + (str(i + 1) if i else "")
        run(
            [
                "docker",
                "exec",
                worker,
                "sed",
                "-i",
                f"s/kind-external-load-balancer/{test_context.common_config.leading_api}/g",
                "/etc/kubernetes/kubelet.conf",
            ],
            operation="redirect worker API endpoint",
        )
        run(
            ["docker", "exec", worker, "systemctl", "restart", "kubelet"],
            operation="restart redirected kubelet",
        )


def redirect_kubectl():
    with (
        wall_timeout("inspect Docker node", limit("request")),
        closing(docker.from_env(timeout=remaining(limit("request")))) as client,
    ):
        cp_port = client.containers.get("kind-control-plane").attrs["NetworkSettings"][
            "Ports"
        ]["6443/tcp"][0]["HostPort"]
        balancer_port = client.containers.get("kind-external-load-balancer").attrs[
            "NetworkSettings"
        ]["Ports"]["6443/tcp"][0]["HostPort"]
    target = Path(os.environ["KUBECONFIG"])
    prefix = "    server: https://127.0.0.1:"
    target.write_text(
        target.read_text().replace(prefix + balancer_port, prefix + cp_port)
    )


def get_apiserver_ports(num_api):
    with (
        wall_timeout("inspect Docker node", limit("request")),
        closing(docker.from_env(timeout=remaining(limit("request")))) as client,
    ):
        return [
            client.containers.get(
                "kind-control-plane" + (str(i + 1) if i else "")
            ).attrs["NetworkSettings"]["Ports"]["6443/tcp"][0]["HostPort"]
            for i in range(num_api)
        ]


def prepare_coordinator(test_context):
    workspace = cache_path("coordinator")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    run(
        ["go", "build", "-o", workspace / "sieve-coordinator", "."],
        cwd=repository_root() / "go/coordinator",
        env={"GOOS": "linux", "GOARCH": "amd64", "CGO_ENABLED": "0", "GOWORK": "off"},
        category="compile",
        operation="compile coordinator",
    )
    if test_context.mode == sieve_modes.TEST:
        (workspace / "configured_field_key_mask.json").write_text(
            json.dumps(test_context.common_config.field_key_mask)
        )
        (workspace / "configured_field_path_mask.json").write_text(
            json.dumps(test_context.common_config.field_path_mask)
        )
        shutil.copy(
            Path(test_context.oracle_dir) / "mask.json",
            workspace / "learned_field_path_mask.json",
        )
    shutil.copy(test_context.test_plan, workspace / "server.yaml")
    run(
        ["docker", "exec", "kind-control-plane", "mkdir", "-p", "/sieve"],
        operation="prepare coordinator directory",
    )
    run(
        ["docker", "cp", workspace, "kind-control-plane:/sieve/coordinator"],
        operation="copy coordinator into node",
    )


def start_coordinator(test_context):
    mode = (
        test_context.mode
        if test_context.mode == sieve_modes.TEST
        else sieve_modes.LEARN
    )
    run(
        [
            "docker",
            "exec",
            "-e",
            f"SIEVE_REQUEST_TIMEOUT={limit('request')}",
            "-e",
            f"SIEVE_OPERATION_TIMEOUT={limit('cluster')}",
            "kind-control-plane",
            "bash",
            "-c",
            f"cd /sieve/coordinator || exit 1; ./sieve-coordinator {mode} > sieve-server.log 2>&1 & echo $! > coordinator.pid",
        ],
        operation="start coordinator",
    )


def stop_coordinator():
    run(
        [
            "docker",
            "exec",
            "kind-control-plane",
            "bash",
            "-c",
            'read -r pid < /sieve/coordinator/coordinator.pid; kill "$pid"',
        ],
        operation="stop coordinator",
        timeout=15,
    )


def setup_kind_cluster(test_context):
    existing = run(
        ["kind", "get", "clusters"],
        operation="check existing clusters",
        capture_output=True,
    ).stdout.splitlines()
    if "kind" in existing:
        raise RuntimeError(
            "A kind cluster already exists; refusing to replace an unowned cluster"
        )
    configuration = create_kind_config(
        test_context.num_apiservers, test_context.num_workers
    )
    platform = "macos-" if sys.platform == "darwin" else ""
    image = f"{test_context.container_registry}/node:{test_context.controller_config.kubernetes_version}-{platform}{test_context.image_tag}"
    _owned.set(True)
    (Path(test_context.result_dir) / "resources.json").write_text(
        json.dumps(
            {"cluster": "kind", "owner_pid": os.getpid(), "image": image}, indent=2
        )
        + "\n"
    )
    for attempt in range(3):
        try:
            run(
                [
                    "kind",
                    "create",
                    "cluster",
                    "--image",
                    image,
                    "--config",
                    configuration,
                ],
                category="cluster",
                operation="create kind cluster",
            )
            run(
                [
                    "docker",
                    "exec",
                    "kind-control-plane",
                    "bash",
                    "-c",
                    "mkdir -p /root/.kube && cp /etc/kubernetes/admin.conf /root/.kube/config",
                ],
                operation="prepare node kubeconfig",
            )
            return
        except ProcessError:
            if attempt == 2 or remaining(limit("cluster")) <= 0:
                raise
            run(
                ["kind", "delete", "cluster"],
                category="cluster",
                operation="remove partial cluster",
            )
            time.sleep(min(5 * (attempt + 1), remaining(limit("cluster"))))


@bounded("cluster")
def setup_cluster(test_context):
    setup_kind_cluster(test_context)
    if "reconnectController" in test_context.action_types:
        redirect_workers(test_context)
        redirect_kubectl()
    load_config()
    api = kubernetes_api(kubernetes.client.CoreV1Api)
    end = time.monotonic() + remaining(limit("cluster"))
    while time.monotonic() < end:
        pods = api.list_namespaced_pod(
            "kube-system",
            watch=False,
            label_selector="component=kube-apiserver",
            _request_timeout=request_timeout(),
        ).items
        if len(pods) == test_context.num_apiservers and all(
            (pod.status.phase == "Running" for pod in pods)
        ):
            break
        time.sleep(min(1, max(0, end - time.monotonic())))
    else:
        raise RuntimeError("API-server readiness deadline expired")
    if test_context.mode != sieve_modes.VANILLA:
        prepare_coordinator(test_context)
        start_coordinator(test_context)
    time.sleep(3)
    run(
        ["kubectl", "apply", "-f", create_configmap(test_context.test_plan)],
        operation="publish Sieve configuration",
    )
    image = f"{test_context.container_registry}/{test_context.controller}:{test_context.image_tag}"
    loaded = run(
        ["kind", "load", "docker-image", image],
        operation="load controller image",
        category="network",
        check=False,
    )
    if loaded.returncode:
        run(
            ["docker", "pull", image],
            operation="pull controller image",
            category="network",
        )
        run(
            ["kind", "load", "docker-image", image],
            operation="load pulled controller image",
            category="network",
        )


def deploy_controller(test_context):
    if test_context.use_csi_driver:
        run(
            ["bash", "./install.sh"],
            cwd=repository_root() / "infra/kubernetes/csi-driver",
            operation="install CSI provisioner",
            category="cluster",
        )
    source = Path(test_context.controller_config_dir) / "deploy"
    target = Path(test_context.result_dir) / "deployment"
    shutil.copytree(source, target)
    manifest = target / asset_path(
        test_context.controller_config.controller_deployment_file_path
    ).relative_to(source)
    manifest.write_text(
        manifest.read_text()
        .replace("${SIEVE-DR}", test_context.container_registry)
        .replace("${SIEVE-DT}", test_context.image_tag)
    )
    run(
        ["bash", "./deploy.sh"],
        cwd=target,
        operation="deploy controller",
        category="cluster",
    )


@bounded("cluster")
def start_controller(test_context):
    deploy_controller(test_context)
    load_config()
    api = kubernetes_api(kubernetes.client.CoreV1Api)
    end = time.monotonic() + remaining(limit("cluster"))
    while time.monotonic() < end:
        pods = api.list_namespaced_pod(
            test_context.common_config.namespace,
            watch=False,
            label_selector="sievetag=" + test_context.controller,
            _request_timeout=request_timeout(),
        ).items
        if pods and any(
            (
                pod.status.phase == "Running"
                and any(
                    (
                        condition.type == "Ready" and condition.status == "True"
                        for condition in pod.status.conditions or []
                    )
                )
                for pod in pods
            )
        ):
            break
        time.sleep(min(1, max(0, end - time.monotonic())))
    else:
        raise RuntimeError("Controller readiness deadline expired")
    for port in get_apiserver_ports(test_context.num_apiservers):
        for crd in test_context.controller_config.custom_resource_definitions:
            run(
                [
                    "kubectl",
                    "get",
                    crd,
                    "-s",
                    f"https://127.0.0.1:{port}",
                    "--ignore-not-found=true",
                ],
                operation="prime API-server CRD instrumentation",
            )


def teardown_cluster():
    if _owned.get():
        run(
            ["kind", "delete", "cluster"],
            operation="delete owned cluster",
            category="cleanup",
        )
        _owned.set(False)


def generate_testing_cluster_config(mode, controller_config, test_plan, test_workload):
    num_apiservers = 1
    num_workers = 2
    use_csi_driver = False
    if test_workload in controller_config.test_setting:
        if "num_apiservers" in controller_config.test_setting[test_workload]:
            num_apiservers = controller_config.test_setting[test_workload][
                "num_apiservers"
            ]
        if "num_workers" in controller_config.test_setting[test_workload]:
            num_workers = controller_config.test_setting[test_workload]["num_workers"]
        if "use_csi_driver" in controller_config.test_setting[test_workload]:
            use_csi_driver = controller_config.test_setting[test_workload][
                "use_csi_driver"
            ]
    return (num_apiservers, num_workers, use_csi_driver)
