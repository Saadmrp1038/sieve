"""Disturb an owned Secret without updating its StringSecret parent."""

import base64
import json

import workload as lifecycle

from sieve_tool.testing.child_repair import Mutation, delete_child, get, kubectl, run


class SecretTarget:
    def setup(self, wait):
        def workers_ready():
            logs = kubectl(
                "logs", "-l", "sievetag=kubernetes-secret-generator", "--tail=-1"
            )
            controllers = set()
            for line in logs.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("msg") == "Starting workers":
                    controllers.add(event.get("controller"))
            return {"stringsecret-controller", "secret-controller"}.issubset(
                controllers
            )

        wait(workers_ready, "informer workers ready")
        lifecycle.apply("initial")

    def observe(self):
        return {
            "parent_after": get("stringsecret", lifecycle.NAME),
            "child_after": get("secret", lifecycle.NAME),
        }

    def healthy(self, observation):
        owner, child = observation["parent_after"], observation["child_after"]
        data = child.get("data", {})
        try:
            password = base64.b64decode(data.get("password", ""), validate=True).decode(
                "ascii"
            )
            marker = base64.b64decode(data.get("marker", ""), validate=True).decode(
                "ascii"
            )
        except ValueError:
            return False
        return (
            marker == "initial"
            and len(password) == 16
            and all(char in "0123456789abcdef" for char in password)
            and owner.get("status", {}).get("secret", {}).get("uid")
            == child["metadata"]["uid"]
        )

    def mutate(self, workload, child):
        if workload == "delete-child":
            return delete_child("secrets", lifecycle.NAME, child["metadata"]["uid"])
        result = json.loads(
            kubectl(
                "patch",
                "secret",
                lifecycle.NAME,
                "--type=merge",
                "-p",
                json.dumps({"data": {"marker": "ZHJpZnQ="}}),
                "-o",
                "json",
            )
        )
        return Mutation(result, result["data"].get("marker") == "ZHJpZnQ=")


def main(arguments):
    return run(arguments, SecretTarget(), timeout=120)
