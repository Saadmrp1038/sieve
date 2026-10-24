import sys
import os


from sieve_tool.testing.driver import new_built_in_workload
from sieve_tool.common.common import RUNNING, TERMINATED

test_cases = {
    "recreate": new_built_in_workload()
    .cmd(
        "kubectl apply -f controllers/casskop-operator/workloads/cassandra-configmap-v1.yaml"
    )
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/cc-1.yaml")
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-0", RUNNING)
    .cmd("kubectl delete CassandraCluster cassandra-cluster")
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-0", TERMINATED, timeout=20)
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/cc-1.yaml")
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-0", RUNNING),
    "reducepdb": new_built_in_workload(110)
    .cmd(
        "kubectl apply -f controllers/casskop-operator/workloads/cassandra-configmap-v1.yaml"
    )
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/cc-2.yaml")
    .wait(60)
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/cc-1.yaml"),
    "scaledown-to-zero": new_built_in_workload()
    .cmd(
        "kubectl apply -f controllers/casskop-operator/workloads/cassandra-configmap-v1.yaml"
    )
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/nodes-2.yaml")
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-0", RUNNING)
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-1", RUNNING)
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/nodes-1.yaml")
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-0", RUNNING)
    .wait_for_pod_status("cassandra-cluster-dc1-rack1-1", TERMINATED)
    .cmd("kubectl apply -f controllers/casskop-operator/workloads/nodes-0.yaml"),
}

test_cases[sys.argv[1]].run(sys.argv[2])
