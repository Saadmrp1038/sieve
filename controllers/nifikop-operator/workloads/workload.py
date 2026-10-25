import sys

from sieve_tool.common.common import RUNNING, TERMINATED
from sieve_tool.testing.driver import new_built_in_workload

test_cases = {
    "change-config": new_built_in_workload(60)
    .cmd(["kubectl", "apply", "-f", "controllers/nifikop-operator/workloads/nc-1.yaml"])
    .wait_for_pod_status("simplenifi-1-*", RUNNING, 220)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    )
    .cmd(
        [
            "kubectl",
            "apply",
            "-f",
            "controllers/nifikop-operator/workloads/nc-config.yaml",
        ]
    )
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    )
    .wait(30)
    .wait_for_pod_status("simplenifi-1-*", RUNNING, 120)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    ),
    "recreate": new_built_in_workload(60)
    .cmd(["kubectl", "apply", "-f", "controllers/nifikop-operator/workloads/nc-1.yaml"])
    .wait_for_pod_status("simplenifi-1-*", RUNNING)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    )
    .cmd(["kubectl", "delete", "nificluster", "simplenifi"])
    .wait_for_pod_status("simplenifi-1-*", TERMINATED)
    .cmd(["kubectl", "apply", "-f", "controllers/nifikop-operator/workloads/nc-1.yaml"])
    .wait_for_pod_status("simplenifi-1-*", RUNNING)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    ),
    "scaledown-scaleup": new_built_in_workload(60)
    .cmd(["kubectl", "apply", "-f", "controllers/nifikop-operator/workloads/nc-2.yaml"])
    .wait_for_pod_status("simplenifi-1-*", RUNNING)
    .wait_for_pod_status("simplenifi-2-*", RUNNING)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    )
    .cmd(
        [
            "kubectl",
            "patch",
            "nificluster",
            "simplenifi",
            "--type=json",
            '-p=[{"op": "remove", "path": "/spec/nodes/1"}]',
        ]
    )
    .wait_for_pod_status("simplenifi-2-*", TERMINATED)
    .cmd(
        [
            "kubectl",
            "patch",
            "nificluster",
            "simplenifi",
            "--type=json",
            '-p=[{"op": "add", "path": "/spec/nodes/1", "value": {"id": 2, "nodeConfigGroup": "default_group"}}]',
        ]
    )
    .wait_for_pod_status("simplenifi-2-*", RUNNING)
    .wait_for_cr_condition(
        "nificluster",
        "simplenifi",
        [["metadata/finalizers", ["nificlusters.nifi.orange.com/finalizer"]]],
    ),
}
test_cases[sys.argv[1]].run(sys.argv[2])
