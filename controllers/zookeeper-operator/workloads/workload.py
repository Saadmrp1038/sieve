import sys
import os


from sieve_tool.testing.driver import new_built_in_workload
from sieve_tool.common.common import RUNNING, TERMINATED

test_cases = {
    "recreate": new_built_in_workload()
    .cmd("kubectl apply -f controllers/zookeeper-operator/workloads/zkc-1.yaml")
    .wait_for_pod_status("zookeeper-cluster-0", RUNNING)
    .cmd("kubectl delete ZookeeperCluster zookeeper-cluster")
    .wait_for_pod_status("zookeeper-cluster-0", TERMINATED)
    .wait_for_pvc_status("data-zookeeper-cluster-0", TERMINATED)
    .cmd("kubectl apply -f controllers/zookeeper-operator/workloads/zkc-1.yaml")
    .wait_for_pod_status("zookeeper-cluster-0", RUNNING),
    "scaledown-scaleup": new_built_in_workload()
    .cmd("kubectl apply -f controllers/zookeeper-operator/workloads/zkc-2.yaml")
    .wait_for_pod_status("zookeeper-cluster-1", RUNNING)
    .wait(30)
    .cmd(
        'kubectl patch ZookeeperCluster zookeeper-cluster --type merge -p=\'{"spec":{"replicas":1}}\''
    )
    .wait_for_pod_status("zookeeper-cluster-1", TERMINATED)
    .wait_for_pvc_status("data-zookeeper-cluster-1", TERMINATED)
    .cmd(
        'kubectl patch ZookeeperCluster zookeeper-cluster --type merge -p=\'{"spec":{"replicas":2}}\''
    )
    .wait_for_pod_status("zookeeper-cluster-1", RUNNING),
}

test_cases[sys.argv[1]].run(sys.argv[2])
