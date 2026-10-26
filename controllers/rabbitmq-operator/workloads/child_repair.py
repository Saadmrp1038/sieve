"""Exercise client Service repair without changing RabbitmqCluster."""

import argparse
import json
import math
import time
from pathlib import Path

import yaml

from sieve_tool.process import deadline
from sieve_tool.process import run as run_process

NAME = "rabbitmq-cluster"
SELECTOR = {"app.kubernetes.io/name": NAME}


def kubectl(*arguments, payload=None):
    return run_process(
        ["kubectl", "--namespace", "default", *arguments],
        input=json.dumps(payload) if payload is not None else None,
        capture_output=True,
        category="request",
        operation="RabbitMQ repair kubectl",
    ).stdout


def get(kind):
    raw = kubectl("get", kind, NAME, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw.strip() else None


def main(arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["delete-child", "drift-child"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--repair-timeout", type=float, default=300)
    args = parser.parse_args(arguments)
    if not math.isfinite(args.repair_timeout) or args.repair_timeout <= 0:
        parser.error("repair timeout must be finite and positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    evidence = {"workload": args.workload, "status": "setup_failed"}

    def wait_for(predicate, description):
        end = time.monotonic() + args.repair_timeout
        with deadline(args.repair_timeout):
            while time.monotonic() < end:
                if predicate():
                    return
                time.sleep(min(1, max(0, end - time.monotonic())))
        raise RuntimeError("Timed out: " + description)

    def healthy():
        parent, child, endpoints = (
            get("rabbitmqcluster"),
            get("service"),
            get("endpoints"),
        )
        evidence.update(
            parent_after=parent, child_after=child, endpoints_after=endpoints
        )
        if parent is None or child is None or endpoints is None:
            return False
        return (
            child["spec"].get("selector") == SELECTOR
            and any(
                ref.get("uid") == parent["metadata"]["uid"]
                and ref.get("kind") == "RabbitmqCluster"
                and ref.get("controller") is True
                for ref in child["metadata"].get("ownerReferences", [])
            )
            and any(
                subset.get("addresses")
                and any(port.get("port") == 5672 for port in subset.get("ports", []))
                for subset in endpoints.get("subsets", [])
            )
        )

    def repaired():
        ready = healthy()
        before, after = evidence["parent_before"], evidence["parent_after"]
        if (
            after is None
            or any(
                before["metadata"][key] != after["metadata"][key]
                for key in ("uid", "generation")
            )
            or before["spec"] != after["spec"]
        ):
            evidence["status"] = "parent_changed"
            raise RuntimeError("RabbitmqCluster changed during child repair")
        return ready and (
            args.workload != "delete-child"
            or evidence["child_after"]["metadata"]["uid"]
            != evidence["child_before"]["metadata"]["uid"]
        )

    try:
        parent = yaml.safe_load(Path(__file__).with_name("rmqc-1.yaml").read_text())
        kubectl("apply", "-f", "-", payload=parent)
        wait_for(healthy, "initial Service convergence and ready endpoints")
        evidence["parent_before"] = evidence["parent_after"]
        evidence["child_before"] = evidence["child_after"]
        evidence["status"] = "mutation_failed"
        if args.workload == "delete-child":
            response = kubectl("delete", "service", NAME, "--wait=false", "-o", "json")
        else:
            response = kubectl(
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
                            "value": {"sieve-test-invalid": "true"},
                        }
                    ]
                ),
                "-o",
                "json",
            )
        evidence["mutation_response"] = json.loads(response)
        if (
            evidence["mutation_response"]["metadata"]["uid"]
            != evidence["child_before"]["metadata"]["uid"]
        ):
            raise RuntimeError(
                "Mutation response does not identify the original Service"
            )
        if args.workload == "drift-child" and evidence["mutation_response"]["spec"].get(
            "selector"
        ) != {"sieve-test-invalid": "true"}:
            raise RuntimeError(
                "Service selector drift was not observed in the mutation response"
            )
        evidence["status"] = "repair_timeout"
        wait_for(repaired, "owned Service repair and ready endpoints")
        evidence["status"] = "repaired"
    except Exception as error:
        evidence["error"] = str(error)
        args.output.write_text(str(error) + "\n")
        return 1
    finally:
        args.output.with_name("repair-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0
