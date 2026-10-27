"""Supply independent updates, then check convergence to the final input."""

import argparse
import base64
import json
import math
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sieve_tool.process import deadline, execution
from sieve_tool.testing.child_repair import Resource, get, kubectl, owned_by, ready
from sieve_tool.testing.updates import IndependentUpdates

PHASE_LABEL = "sieve.testing/state"
NAME = "sieve-external-secret"
STORE = "sieve-local-store"
VALUES = ("expected-value", "intermediate-one", "intermediate-two", "final-value")


def wait_for(predicate: Callable[[], bool], description: str, timeout: float) -> None:
    with deadline(timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(min(0.2, max(0, end - time.monotonic())))
    raise RuntimeError("Timed out: " + description)


def setup() -> None:
    kubectl(
        "apply",
        "-f",
        "-",
        payload={
            "apiVersion": "external-secrets.io/v1",
            "kind": "SecretStore",
            "metadata": {"name": STORE, "namespace": "default", "labels": {}},
            "spec": {
                "provider": {
                    "fake": {
                        "data": [
                            {
                                "key": "/sieve/value",
                                "value": value,
                                "version": f"v{i + 1}",
                            }
                            for i, value in enumerate(VALUES)
                        ]
                    }
                }
            },
        },
    )
    kubectl(
        "apply",
        "-f",
        "-",
        payload={
            "apiVersion": "external-secrets.io/v1",
            "kind": "ExternalSecret",
            "metadata": {"name": NAME, "namespace": "default", "labels": {}},
            "spec": {
                "refreshPolicy": "Periodic",
                "refreshInterval": "5s",
                "secretStoreRef": {"kind": "SecretStore", "name": STORE},
                "target": {"name": NAME, "creationPolicy": "Owner"},
                "data": [
                    {
                        "secretKey": "marker",
                        "remoteRef": {"key": "/sieve/value", "version": "v1"},
                    }
                ],
            },
        },
    )


def patch(
    resource: str, name: str, uid: str, phase: str, changes: list[Resource]
) -> Resource:
    current = get(resource, name)
    if current is None or current["metadata"]["uid"] != uid:
        raise RuntimeError("Input identity changed")
    labels = current["metadata"].get("labels")
    label_patch = {
        "op": "add",
        "path": "/metadata/labels/sieve.testing~1state"
        if labels is not None
        else "/metadata/labels",
        "value": phase if labels is not None else {PHASE_LABEL: phase},
    }
    response = json.loads(
        kubectl(
            "patch",
            resource,
            name,
            "--type=json",
            "-p",
            json.dumps(
                [
                    {"op": "test", "path": "/metadata/uid", "value": uid},
                    label_patch,
                    *changes,
                ]
            ),
            "-o",
            "json",
        )
    )
    if not isinstance(response, dict):
        raise RuntimeError("Update response is not a Kubernetes object")
    if (
        response.get("metadata", {}).get("uid") != uid
        or response["metadata"].get("labels", {}).get(PHASE_LABEL) != phase
    ):
        raise RuntimeError("Update response does not establish the requested state")
    return response


def run(arguments: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workload",
        choices=["miss-store-updates", "miss-parent-updates", "miss-child-updates"],
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--update-interval", type=float, default=6)
    parser.add_argument("--repair-timeout", type=float, default=180)
    parser.add_argument("--settle-seconds", type=float, default=15)
    args = parser.parse_args(arguments)
    for name in ("update_interval", "repair_timeout", "settle_seconds"):
        value = getattr(args, name)
        if (
            not math.isfinite(value)
            or value < 0
            or (name != "settle_seconds" and value == 0)
        ):
            parser.error(name + " must be finite and positive (settling may be zero)")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    parent_updates = args.workload == "miss-parent-updates"
    resource, name, input_path, values, expected_value = {
        "miss-store-updates": (
            "secretstore",
            STORE,
            "/spec/provider/fake/data/0/value",
            VALUES,
            VALUES[-1],
        ),
        "miss-parent-updates": (
            "externalsecret",
            NAME,
            "/spec/data/0/remoteRef/version",
            tuple(f"v{i + 1}" for i in range(4)),
            VALUES[-1],
        ),
        "miss-child-updates": (
            "secret",
            NAME,
            "/data/marker",
            tuple(base64.b64encode(v.encode()).decode() for v in VALUES),
            VALUES[0],
        ),
    }[args.workload]
    evidence: Resource = {"workload": args.workload, "status": "setup_failed"}
    source: Resource = {
        "schema_version": 1,
        "workload": args.workload,
        "status": "in_progress",
        "resource_key": f"{resource}/default/{name}",
        "updates": [],
    }

    def save() -> None:
        args.output.with_name("workload-updates.json").write_text(
            json.dumps(source, indent=2) + "\n"
        )
        args.output.with_name("update-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def healthy(value: str) -> bool:
        parent, child = get("externalsecret", NAME), get("secret", NAME)
        evidence.update(parent_after=parent, child_after=child)
        if parent is None or child is None:
            return False
        if "parent_before" in evidence:
            before = evidence["parent_before"]
            if parent["metadata"]["uid"] != before["metadata"]["uid"]:
                raise RuntimeError("Parent identity changed")
            expected = evidence.get("parent_expected") if parent_updates else before
            if expected is not None and (
                parent["metadata"]["generation"] != expected["metadata"]["generation"]
                or parent["spec"] != expected["spec"]
            ):
                raise RuntimeError("Parent changed outside the declared updates")
        if (
            parent_updates
            and "parent_expected" in evidence
            and not parent.get("status", {})
            .get("syncedResourceVersion", "")
            .startswith(str(parent["metadata"]["generation"]) + "-")
        ):
            return False
        return (
            ready(parent)
            and owned_by(child, parent)
            and child.get("data", {}).get("marker")
            == base64.b64encode(value.encode()).decode()
        )

    with execution(log_dir=args.output.parent / "update-commands"):
        try:
            setup()
            wait_for(
                lambda: healthy(VALUES[0]), "initial convergence", args.repair_timeout
            )
            evidence["parent_before"] = evidence["parent_after"]
            original = get(resource, name)
            if original is None:
                raise RuntimeError("Input disappeared")
            evidence["status"] = "updating"
            for index, phase in enumerate("ABCD"):
                response = patch(
                    resource,
                    name,
                    original["metadata"]["uid"],
                    phase,
                    [
                        {
                            "op": "replace",
                            "path": input_path,
                            "value": values[index],
                        },
                    ],
                )
                accepted: Any = response
                for component in input_path.strip("/").split("/"):
                    accepted = (
                        accepted[int(component)]
                        if isinstance(accepted, list)
                        else accepted[component]
                    )
                if accepted != values[index]:
                    raise RuntimeError("Input value update was not accepted")
                source["updates"].append(
                    {
                        "producer": "workload",
                        "state": {"metadata": {"labels": {PHASE_LABEL: phase}}},
                        "response": response,
                    }
                )
                save()
                if index < 3:
                    time.sleep(args.update_interval)
            if parent_updates:
                evidence["parent_expected"] = source["updates"][-1]["response"]
            wait_for(
                lambda: healthy(expected_value),
                "final input convergence",
                args.repair_timeout,
            )
            until = time.monotonic() + args.settle_seconds
            while time.monotonic() < until:
                if not healthy(expected_value):
                    raise RuntimeError("Final input did not remain converged")
                time.sleep(min(0.2, max(0, until - time.monotonic())))
            final_input = get(resource, name)
            if (
                final_input is None
                or final_input["metadata"].get("labels", {}).get(PHASE_LABEL) != "D"
                or final_input["metadata"]["uid"] != original["metadata"]["uid"]
            ):
                raise RuntimeError("Final input changed after the external updates")
            evidence["input_after"] = final_input
            evidence.update(status="converged", settle_seconds=args.settle_seconds)
            IndependentUpdates.model_validate({**source, "status": "completed"})
            source["status"] = "completed"
        except Exception as error:
            evidence["error"] = str(error)
            source["status"] = "failed"
            args.output.write_text(str(error) + "\n")
            return 1
        finally:
            save()
    args.output.write_text("FINISH-SIEVE-TEST\n")
    return 0
