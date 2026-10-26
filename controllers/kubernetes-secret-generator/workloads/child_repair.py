"""Disturb an owned Secret without updating its StringSecret parent."""

import argparse
import json
import math
import time
from pathlib import Path

import workload as lifecycle

from sieve_tool.process import deadline


def main(arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["delete-child", "drift-child"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--repair-timeout", type=float, default=120)
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

    def repaired():
        evidence["parent_after"] = lifecycle.get("stringsecret")
        evidence["child_after"] = lifecycle.get("secret")
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
            raise RuntimeError("StringSecret changed during child repair")
        child = evidence["child_after"]
        return (
            child is not None
            and any(
                ref.get("uid") == before["metadata"]["uid"]
                and ref.get("kind") == "StringSecret"
                and ref.get("controller") is True
                for ref in child["metadata"].get("ownerReferences", [])
            )
            and (
                args.workload != "delete-child"
                or child["metadata"]["uid"]
                != evidence["child_before"]["metadata"]["uid"]
            )
            and lifecycle.consistent("initial")
        )

    try:

        def workers_ready():
            logs = lifecycle.kubectl(
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

        wait_for(workers_ready, "informer workers ready")
        lifecycle.apply("initial")
        wait_for(lambda: lifecycle.consistent("initial"), "initial convergence")
        evidence["parent_before"] = lifecycle.get("stringsecret")
        evidence["child_before"] = lifecycle.get("secret")
        evidence["status"] = "mutation_failed"
        if args.workload == "delete-child":
            response = lifecycle.kubectl(
                "delete", "secret", lifecycle.NAME, "--wait=false", "-o", "json"
            )
        else:
            response = lifecycle.kubectl(
                "patch",
                "secret",
                lifecycle.NAME,
                "--type=merge",
                "-p",
                json.dumps({"data": {"marker": "ZHJpZnQ="}}),
                "-o",
                "json",
            )
        evidence["mutation_response"] = json.loads(response)
        mutation = evidence["mutation_response"]
        if mutation["metadata"]["uid"] != evidence["child_before"]["metadata"]["uid"]:
            raise RuntimeError(
                "Mutation response does not identify the original Secret"
            )
        if (
            args.workload == "drift-child"
            and mutation["data"].get("marker") != "ZHJpZnQ="
        ):
            raise RuntimeError(
                "Secret data drift was not observed in the mutation response"
            )
        evidence["status"] = "repair_timeout"
        wait_for(repaired, "owned Secret repair")
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
