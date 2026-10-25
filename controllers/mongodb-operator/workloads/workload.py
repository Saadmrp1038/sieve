import sys

from sieve_tool.common.common import RUNNING, TERMINATED
from sieve_tool.testing.driver import new_built_in_workload

test_cases = {
    "recreate": new_built_in_workload(70)
    .cmd(["kubectl", "apply", "-f", "controllers/mongodb-operator/workloads/cr.yaml"])
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING)
    .cmd(["kubectl", "delete", "PerconaServerMongoDB", "mongodb-cluster"])
    .wait_for_pod_status("mongodb-cluster-rs0-*", TERMINATED)
    .wait_for_pvc_status("mongod-data-mongodb-cluster-rs0-*", TERMINATED)
    .cmd(["kubectl", "apply", "-f", "controllers/mongodb-operator/workloads/cr.yaml"])
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING),
    "disable-enable-shard": new_built_in_workload(70)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/mongodb-operator/workloads/cr-shard.yaml",
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING)
    .wait_for_pod_status("mongodb-cluster-cfg-2", RUNNING)
    .wait_for_pod_status("mongodb-cluster-mongos-*", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type",
            "merge",
            '-p={"spec":{"sharding":{"enabled":false}}}',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-cfg-2", TERMINATED)
    .wait_for_pod_status("mongodb-cluster-mongos-*", TERMINATED)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type",
            "merge",
            '-p={"spec":{"sharding":{"enabled":true}}}',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-cfg-2", RUNNING)
    .wait_for_pod_status("mongodb-cluster-mongos-*", RUNNING),
    "disable-enable-arbiter": new_built_in_workload(70)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/mongodb-operator/workloads/cr-arbiter.yaml",
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-3", RUNNING, 150)
    .wait_for_pod_status("mongodb-cluster-rs0-arbiter-0", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type=json",
            '-p=[{"op": "replace", "path": "/spec/replsets/0/arbiter/enabled", "value": false}]',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-arbiter-0", TERMINATED)
    .wait_for_pod_status("mongodb-cluster-rs0-4", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type=json",
            '-p=[{"op": "replace", "path": "/spec/replsets/0/arbiter/enabled", "value": true}]',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-arbiter-0", RUNNING)
    .wait_for_pod_status("mongodb-cluster-rs0-4", TERMINATED),
    "run-cert-manager": new_built_in_workload(70)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "https://github.com/jetstack/cert-manager/releases/download/v0.15.1/cert-manager.yaml",
            "--validate=false",
        ]
    )
    .wait_for_pod_status("cert-manager-webhook-*", RUNNING, namespace="cert-manager")
    .cmd(["kubectl", "apply", "-f", "controllers/mongodb-operator/workloads/cr.yaml"])
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING),
    "scaleup-scaledown": new_built_in_workload(70)
    .cmd(["kubectl", "apply", "-f", "controllers/mongodb-operator/workloads/cr.yaml"])
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type=json",
            '-p=[{"op": "replace", "path": "/spec/replsets/0/size", "value": 5}]',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-4", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type=json",
            '-p=[{"op": "replace", "path": "/spec/replsets/0/size", "value": 3}]',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-3", TERMINATED),
    "disable-enable-shard-brittle": new_built_in_workload(70)
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/mongodb-operator/workloads/cr-shard.yaml",
        ]
    )
    .wait_for_pod_status("mongodb-cluster-rs0-2", RUNNING)
    .wait_for_pod_status("mongodb-cluster-cfg-2", RUNNING)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type",
            "merge",
            '-p={"spec":{"sharding":{"enabled":false}}}',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-cfg-2", TERMINATED)
    .cmd(
        [
            "kubectl",
            "patch",
            "PerconaServerMongoDB",
            "mongodb-cluster",
            "--type",
            "merge",
            '-p={"spec":{"sharding":{"enabled":true}}}',
        ]
    )
    .wait_for_pod_status("mongodb-cluster-cfg-2", RUNNING),
}
test_cases[sys.argv[1]].run(sys.argv[2])
