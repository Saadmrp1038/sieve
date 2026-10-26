import sys

from sieve_tool.common.common import RUNNING, TERMINATED
from sieve_tool.testing.driver import new_built_in_workload

test_cases = {
    "scaleup": new_built_in_workload()
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/rabbitmq-operator/workloads/rmqc-1.yaml",
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-0", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "RabbitmqCluster",
            "rabbitmq-cluster",
            "--type",
            "merge",
            '-p={"spec":{"replicas":3}}',
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-2", RUNNING)
    .cmd(
        [
            "kubectl",
            "rollout",
            "status",
            "statefulset/rabbitmq-cluster-server",
            "--timeout=180s",
        ],
        timeout=190,
    ),
    "recreate": new_built_in_workload()
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/rabbitmq-operator/workloads/rmqc-1.yaml",
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-0", RUNNING)
    .cmd(["kubectl", "delete", "RabbitmqCluster", "rabbitmq-cluster"])
    .wait_for_pod_status("rabbitmq-cluster-server-0", TERMINATED)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/rabbitmq-operator/workloads/rmqc-1.yaml",
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-0", RUNNING),
    # Historical reproduction: this pinned operator explicitly rejects scale-down.
    "scaleup-scaledown": new_built_in_workload(70)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/rabbitmq-operator/workloads/rmqc-1.yaml",
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-0", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "RabbitmqCluster",
            "rabbitmq-cluster",
            "--type",
            "merge",
            '-p={"spec":{"replicas":3}}',
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-2", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "RabbitmqCluster",
            "rabbitmq-cluster",
            "--type",
            "merge",
            '-p={"spec":{"replicas":2}}',
        ]
    ),
    "resize-pvc": new_built_in_workload(80)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/rabbitmq-operator/workloads/rmqc-1.yaml",
        ]
    )
    .wait_for_pod_status("rabbitmq-cluster-server-0", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "RabbitmqCluster",
            "rabbitmq-cluster",
            "--type",
            "merge",
            '-p={"spec":{"persistence":{"storage":"15Gi"}}}',
        ]
    )
    .wait_for_sts_storage_size("rabbitmq-cluster-server", "15Gi"),
}
if sys.argv[1] in ("delete-child", "drift-child"):
    from child_repair import main

    raise SystemExit(main(sys.argv[1:]))
test_cases[sys.argv[1]].run(sys.argv[2])
