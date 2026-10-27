"""Exercise client Service repair without changing RabbitmqCluster."""

import json
from pathlib import Path

import yaml

from sieve_tool.testing.child_repair import Mutation, delete_child, get, kubectl, run

NAME = "rabbitmq-cluster"
SELECTOR = {"app.kubernetes.io/name": NAME}
DRIFT_SELECTOR = {"sieve-test-invalid": "true"}


class ServiceTarget:
    def setup(self, wait):
        parent = yaml.safe_load(Path(__file__).with_name("rmqc-1.yaml").read_text())
        kubectl("apply", "-f", "-", payload=parent)

    def observe(self):
        return {
            "parent_after": get("rabbitmqcluster", NAME),
            "child_after": get("service", NAME),
            "endpoints_after": get("endpoints", NAME),
        }

    def healthy(self, observation):
        child, endpoints = observation["child_after"], observation["endpoints_after"]
        return (
            child["spec"].get("selector") == SELECTOR
            and endpoints is not None
            and any(
                subset.get("addresses")
                and any(port.get("port") == 5672 for port in subset.get("ports", []))
                for subset in endpoints.get("subsets", [])
            )
        )

    def mutate(self, workload, child):
        if workload == "delete-child":
            return delete_child("services", NAME, child["metadata"]["uid"])
        result = json.loads(
            kubectl(
                "patch",
                "service",
                NAME,
                "--type=json",
                "-p",
                json.dumps(
                    [
                        {
                            "op": "replace",
                            "path": "/spec/selector",
                            "value": DRIFT_SELECTOR,
                        }
                    ]
                ),
                "-o",
                "json",
            )
        )
        return Mutation(result, result["spec"].get("selector") == DRIFT_SELECTOR)


def main(arguments):
    return run(arguments, ServiceTarget(), timeout=300)
