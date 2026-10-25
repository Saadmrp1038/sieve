import sys
import os


from sieve_tool.testing.driver import new_built_in_workload
from sieve_tool.common.common import RUNNING, TERMINATED

test_cases = {
    "recreate": new_built_in_workload()
    .cmd("kubectl apply -f controllers/cassandra-operator/workloads/cdc-1.yaml")
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-0", RUNNING)
    .cmd("kubectl delete CassandraDataCenter cassandra-datacenter")
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-0", TERMINATED, 10)
    .cmd("kubectl apply -f controllers/cassandra-operator/workloads/cdc-1.yaml")
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-0", RUNNING),
    "scaledown-scaleup": new_built_in_workload()
    .cmd("kubectl apply -f controllers/cassandra-operator/workloads/cdc-2.yaml")
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-1", RUNNING, 200)
    .cmd(
        'kubectl patch CassandraDataCenter cassandra-datacenter --type merge -p=\'{"spec":{"nodes":1}}\''
    )
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-1", TERMINATED, 150)
    .wait_for_pvc_status(
        "data-volume-cassandra-test-cluster-dc1-rack1-1",
        TERMINATED,
        20,
    )
    .cmd(
        'kubectl patch CassandraDataCenter cassandra-datacenter --type merge -p=\'{"spec":{"nodes":2}}\''
    )
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-1", RUNNING, 150),
    "scaledown-scaleup-brittle": new_built_in_workload()
    .cmd("kubectl apply -f controllers/cassandra-operator/workloads/cdc-2.yaml")
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-1", RUNNING, 200)
    .cmd(
        'kubectl patch CassandraDataCenter cassandra-datacenter --type merge -p=\'{"spec":{"nodes":1}}\''
    )
    .wait(50)
    .cmd(
        'kubectl patch CassandraDataCenter cassandra-datacenter --type merge -p=\'{"spec":{"nodes":2}}\''
    )
    .wait_for_pod_status("cassandra-test-cluster-dc1-rack1-1", RUNNING, 150),
}

test_cases[sys.argv[1]].run(sys.argv[2])
