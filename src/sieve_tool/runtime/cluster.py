import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import json
import yaml
import docker
import kubernetes

from sieve_tool.common.common import *
from sieve_tool.paths import repository_root, cache_path


def create_configmap(test_plan):
    test_plan_content = json.dumps(yaml.safe_load(Path(test_plan).read_text()))
    configmap = {}
    configmap["apiVersion"] = "v1"
    configmap["kind"] = "ConfigMap"
    configmap["metadata"] = {"name": "sieve-testing-global-config"}
    configmap["data"] = {"sieveTestPlan": test_plan_content}
    configmap_path = "{}-configmap.yaml".format(test_plan[:-5])
    yaml.dump(configmap, open(configmap_path, "w"), sort_keys=False)
    return configmap_path


def create_kind_config(num_apiservers, num_workers):
    kind_config_dir = ".cache/kind"
    os.makedirs(kind_config_dir, exist_ok=True)
    kind_config_filename = os.path.join(
        kind_config_dir,
        "kind-{}a-{}w.yaml".format(
            str(num_apiservers),
            str(num_workers),
        ),
    )
    with open(kind_config_filename, "w") as kind_config_file:
        kind_config_file.writelines(
            ["kind: Cluster\n", "apiVersion: kind.x-k8s.io/v1alpha4\n", "nodes:\n"]
        )
        for i in range(num_apiservers):
            kind_config_file.write("- role: control-plane\n")
        for i in range(num_workers):
            kind_config_file.write("- role: worker\n")
    return kind_config_filename


def redirect_workers(test_context: TestContext):
    leading_api = test_context.common_config.leading_api
    for i in range(test_context.num_workers):
        worker = "kind-worker" + (str(i + 1) if i > 0 else "")
        os_system(
            "docker exec {} bash -c \"sed -i 's/kind-external-load-balancer/{}/g' /etc/kubernetes/kubelet.conf\"".format(
                worker, leading_api
            )
        )
        os_system('docker exec {} bash -c "systemctl restart kubelet"'.format(worker))


def redirect_kubectl():
    client = docker.from_env(version="auto")
    cp_port = client.containers.get("kind-control-plane").attrs["NetworkSettings"][
        "Ports"
    ]["6443/tcp"][0]["HostPort"]
    balancer_port = client.containers.get("kind-external-load-balancer").attrs[
        "NetworkSettings"
    ]["Ports"]["6443/tcp"][0]["HostPort"]
    kube_config = os.getenv("KUBECONFIG")
    target_prefix = "    server: https://127.0.0.1:"
    fin = open(kube_config)
    data = fin.read()
    # print("Replace {} w {} in {}".format(balancer_port, cp_port, kube_config))
    data = data.replace(target_prefix + balancer_port, target_prefix + cp_port)
    fin.close()
    fin = open(kube_config, "w")
    fin.write(data)
    fin.close()


def get_apiserver_ports(num_api):
    client = docker.from_env(version="auto")
    ports = []
    for i in range(num_api):
        container_name_prefix = "kind-control-plane"
        suffix = str(i + 1) if i > 0 else ""
        cp_port = client.containers.get(container_name_prefix + suffix).attrs[
            "NetworkSettings"
        ]["Ports"]["6443/tcp"][0]["HostPort"]
        ports.append(cp_port)
    return ports


def prepare_coordinator(test_context: TestContext):
    workspace = cache_path("coordinator")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    subprocess.run(
        ["go", "build", "-o", str(workspace / "sieve-coordinator"), "."],
        cwd=repository_root() / "go/coordinator",
        env={
            **os.environ,
            "GOOS": "linux",
            "GOARCH": "amd64",
            "CGO_ENABLED": "0",
            "GOWORK": "off",
        },
        check=True,
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
    subprocess.run(
        ["docker", "exec", "kind-control-plane", "mkdir", "-p", "/sieve"], check=True
    )
    subprocess.run(
        ["docker", "cp", str(workspace), "kind-control-plane:/sieve/coordinator"],
        check=True,
    )


def start_coordinator(test_context: TestContext):
    mode = (
        test_context.mode
        if test_context.mode == sieve_modes.TEST
        else sieve_modes.LEARN
    )
    subprocess.run(
        [
            "docker",
            "exec",
            "kind-control-plane",
            "bash",
            "-c",
            f"cd /sieve/coordinator || exit 1; ./sieve-coordinator {mode} > sieve-server.log 2>&1 & echo $! > coordinator.pid",
        ],
        check=True,
    )


def stop_coordinator():
    subprocess.run(
        [
            "docker",
            "exec",
            "kind-control-plane",
            "bash",
            "-c",
            'read -r pid < /sieve/coordinator/coordinator.pid; kill "$pid"',
        ],
        check=True,
    )


def setup_kind_cluster(test_context: TestContext):
    kind_config = create_kind_config(
        test_context.num_apiservers, test_context.num_workers
    )
    platform = ""
    if sys.platform == "darwin":
        platform = "macos-"
    k8s_container_registry = test_context.container_registry
    k8s_image_tag = (
        test_context.controller_config.kubernetes_version
        + "-"
        + platform
        + test_context.image_tag
    )
    retry_cnt = 0
    # Retry cluster creation for 5 times.
    while retry_cnt < 5:
        try:
            os_system("kind delete cluster")
            # Sleep here in case if the machine is slow and kind cluster deletion is not done before creating a new cluster.
            time.sleep(10 * retry_cnt)
            if retry_cnt == 0:
                print("Trying to create kind cluster")
            else:
                print(
                    "Retrying to create kind cluster; retry count {}".format(retry_cnt)
                )
            retry_cnt += 1
            os_system(
                "kind create cluster --image {}/node:{} --config {}".format(
                    k8s_container_registry, k8s_image_tag, kind_config
                )
            )
            os_system(
                "docker exec kind-control-plane bash -c 'mkdir -p /root/.kube/ && cp /etc/kubernetes/admin.conf /root/.kube/config'"
            )
            return
        except Exception:
            print(traceback.format_exc())
    raise RuntimeError("Failed to create kind cluster after five attempts")


def setup_cluster(test_context: TestContext):
    """
    Set up the kind cluster for testing and wait until the control plane is ready.
    """
    setup_kind_cluster(test_context)
    print("\n\n")

    # When testing stale-state, we need to pause the apiserver
    # if workers talks to the paused apiserver, the whole cluster will be slowed down.
    # In a multi-apiserver set up (HA mode), each worker (kubelet) talks to a load balancer
    # which might forward the request to any backend apiserver.
    # We want to focus on testing how the controller handles staleness
    # so here we redirect the workers to an apiserver (configurable in config.json)
    # which Sieve will NOT slow down later.
    if "reconnectController" in test_context.action_types:
        cprint(
            "Redirecting workers and kubectl to the leading API server...",
            bcolors.OKGREEN,
        )
        redirect_workers(test_context)  # Redirect the kubelet on each worker node.
        redirect_kubectl()  # Redirect the local kubectl.
        ok("Redirection done")

    kubernetes.config.load_kube_config()
    core_v1 = kubernetes.client.CoreV1Api()

    # Then we wait apiservers to be ready.
    print("Waiting for apiservers to be ready...")
    apiserver_list = []
    for i in range(test_context.num_apiservers):
        apiserver_name = "kube-apiserver-kind-control-plane" + (
            "" if i == 0 else str(i + 1)
        )
        apiserver_list.append(apiserver_name)
    # TODO: this can be better replaced by a watch.
    for tick in range(600):
        created = core_v1.list_namespaced_pod(
            "kube-system", watch=False, label_selector="component=kube-apiserver"
        ).items
        if len(created) == len(apiserver_list) and len(created) == len(
            [item for item in created if item.status.phase == "Running"]
        ):
            break
        time.sleep(1)

    if test_context.mode != sieve_modes.VANILLA:
        # Start the Sieve server.
        # In learn mode, it will record the controller events used for generating test plans.
        # In test mode, it reads the test plan and injects fault accordingly.
        prepare_coordinator(test_context)
        cprint("Setting up Sieve server...", bcolors.OKGREEN)
        start_coordinator(test_context)
        ok("Sieve server set up")

    time.sleep(3)  # Ensure that every apiserver will see the configmap is created.

    # We store the test plan into a configmap and create this configmap
    # so that the instrumentation at the apiserver side can also read the test plan
    # (by examining each incoming confimap creation event).
    # This is a bit hacky.
    # TODO: find a more elegant way to communicate with the instrumented apiserver.
    configmap = create_configmap(test_context.test_plan)
    os_system("kubectl apply -f {}".format(configmap))

    # Preload controller image to kind nodes.
    # This makes it faster to start the controller.
    image = "{}/{}:{}".format(
        test_context.container_registry,
        test_context.controller,
        test_context.image_tag,
    )
    kind_load_cmd = "kind load docker-image {}".format(image)
    print("Loading image {} to kind nodes...".format(image))
    if os_system(kind_load_cmd, early_exit=False) != 0:
        print("Cannot load image {} locally, try to pull from remote".format(image))
        os_system("docker pull {}".format(image))
        os_system(kind_load_cmd)


def deploy_controller(test_context: TestContext):
    # Install csi driver if some controller needs it.
    if test_context.use_csi_driver:
        print("Installing csi provisioner...")
        org_dir = os.getcwd()
        os.chdir("infra/kubernetes/csi-driver")
        os_system("./install.sh")
        os.chdir(org_dir)

    deployment_file = test_context.controller_config.controller_deployment_file_path

    # Backup the provided deployment file.
    backup_deployment_file = deployment_file + ".bkp"
    shutil.copyfile(deployment_file, backup_deployment_file)

    # Modify the marked container_registry and image_tag in the deployment yaml.
    # TODO: there should be a better way to parameterize the deployment yaml file.
    fin = open(deployment_file)
    data = fin.read()
    data = data.replace("${SIEVE-DR}", test_context.container_registry)
    data = data.replace("${SIEVE-DT}", test_context.image_tag)
    fin.close()
    fin = open(deployment_file, "w")
    fin.write(data)
    fin.close()

    # Run the provided deploy script.
    org_dir = os.getcwd()
    os.chdir(deploy_directory(test_context))
    os_system("./deploy.sh")
    os.chdir(org_dir)

    # Restore deployment file for later use.
    shutil.copyfile(backup_deployment_file, deployment_file)
    os.remove(backup_deployment_file)


def start_controller(test_context: TestContext):
    """
    Deploy the controller and wait until the controller becomes ready
    """
    controller = test_context.controller
    num_apiservers = test_context.num_apiservers
    deploy_controller(test_context)

    kubernetes.config.load_kube_config()
    core_v1 = kubernetes.client.CoreV1Api()

    # Wait for controller pod to be ready
    # TODO: this can be better replaced by a watch.
    print("Wait for the controller pod to be ready...")
    pod_ready = False
    for tick in range(600):
        controller_pod = core_v1.list_namespaced_pod(
            test_context.common_config.namespace,
            watch=False,
            label_selector="sievetag=" + controller,
        ).items
        if len(controller_pod) >= 1:
            if controller_pod[0].status.phase == "Running":
                pod_ready = True
                break
        time.sleep(1)
    if not pod_ready:
        fail("waiting for the controller pod to be ready")
        raise Exception("Wait timeout after 600 seconds")

    # Issue a get crd operation to each apiserver (i.e., addr)
    # to make sure the instrumentation at the apiserver side
    # can observe the crd change.
    # This is necessary because sometimes the instrumentation at the apiserver
    # side needs to talk to the Sieve server when certain changes happen to
    # the custom resource objects (depending on the test plan).
    apiserver_addr_list = []
    apiserver_ports = get_apiserver_ports(num_apiservers)
    for port in apiserver_ports:
        apiserver_addr_list.append("https://127.0.0.1:" + port)
    for addr in apiserver_addr_list:
        for crd in test_context.controller_config.custom_resource_definitions:
            os_system("kubectl get {} -s {} --ignore-not-found=true".format(crd, addr))


def teardown_cluster():
    os_system("kind delete cluster")


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
    return (
        num_apiservers,
        num_workers,
        use_csi_driver,
    )
