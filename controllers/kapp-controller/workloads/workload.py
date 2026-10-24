import sys
import os


from sieve_tool.testing.driver import new_built_in_workload
from sieve_tool.common.common import RUNNING, TERMINATED

test_cases = {
    "create": new_built_in_workload()
    .cmd("kubectl apply -f controllers/kapp-controller/workloads/app.yml")
    .wait_for_pod_status("simple-app-*", RUNNING),
}

test_cases[sys.argv[1]].run(sys.argv[2])
