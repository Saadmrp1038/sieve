"""Exercise generated data, status references, garbage collection, and owner UIDs."""

import base64
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

import yaml

NAME = "sieve-generated-secret"
TIMEOUT = 120


def kubectl(*args, payload=None):
    result = subprocess.run(
        ["kubectl", "--namespace", "default", *args],
        input=json.dumps(payload) if payload is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def get(kind):
    raw = kubectl("get", kind, NAME, "--ignore-not-found", "-o", "json")
    return json.loads(raw) if raw.strip() else None


def wait_for(description, predicate):
    deadline = time.monotonic() + TIMEOUT
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                print("PASS: " + description, flush=True)
                return value
        except (RuntimeError, KeyError, ValueError) as error:
            last_error = str(error)
        time.sleep(1)
    raise RuntimeError(
        "Conditional wait timeout: " + description + " " + str(last_error)
    )


def apply(marker):
    kubectl(
        "apply",
        "-f",
        "-",
        payload={
            "apiVersion": "secretgenerator.mittwald.de/v1alpha1",
            "kind": "StringSecret",
            "metadata": {"name": NAME, "namespace": "default"},
            "spec": {
                "data": {"marker": marker},
                "fields": [
                    {"fieldName": "password", "encoding": "hex", "length": "16"}
                ],
                "forceRegenerate": True,
            },
        },
    )


def consistent(marker):
    owner, secret = get("stringsecret"), get("secret")
    if owner is None or secret is None:
        return False
    data = secret.get("data", {})
    password = base64.b64decode(data.get("password", "")).decode()
    actual_marker = base64.b64decode(data.get("marker", "")).decode()
    owners = secret["metadata"].get("ownerReferences", [])
    reference = owner.get("status", {}).get("secret", {})
    return (
        actual_marker == marker
        and len(password) == 16
        and all(char in "0123456789abcdef" for char in password)
        and any(ref["uid"] == owner["metadata"]["uid"] for ref in owners)
        and reference.get("uid") == secret["metadata"]["uid"]
    )


def run(allow_missed_intermediate_state=False, allow_missed_initial_state=False):
    # Pod Running precedes informer startup. Applying the first object too
    # early turns its first notification into Sync, moving an Added trigger to
    # the later recreation and leaving no subsequent event to release it.
    def workers_started():
        logs = kubectl(
            "logs", "-l", "sievetag=kubernetes-secret-generator", "--tail=-1"
        )
        ready = set()
        for line in logs.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("msg") == "Starting workers":
                ready.add(event.get("controller"))
        return {"stringsecret-controller", "secret-controller"}.issubset(ready)

    wait_for("StringSecret and Secret informer workers ready", workers_started)
    apply("initial")
    if allow_missed_initial_state:
        # A plan pausing on CR creation needs the next user update to release
        # reconciliation. The initial Secret is intentionally not required yet.
        time.sleep(5)
        print("Initial convergence deferred during missed-state injection", flush=True)
    else:
        wait_for(
            "initial generated Secret, ownership, and status",
            lambda: consistent("initial"),
        )
    original_uid = get("stringsecret")["metadata"]["uid"]
    apply("updated")
    if allow_missed_intermediate_state:
        # A missed-state plan deliberately prevents reconciliation until a later
        # update or deletion arrives. Waiting for the intermediate Secret here
        # would prevent that release event and deadlock the test driver.
        time.sleep(5)
        print(
            "Intermediate convergence deferred during missed-state injection",
            flush=True,
        )
    else:
        wait_for(
            "updated generated Secret, ownership, and status",
            lambda: consistent("updated"),
        )
    kubectl("delete", "stringsecret", NAME, "--wait=true", "--timeout=60s")
    wait_for("owned Secret garbage-collected", lambda: get("secret") is None)
    apply("recreated")
    wait_for(
        "recreated generated Secret, ownership, and status",
        lambda: consistent("recreated"),
    )
    assert get("stringsecret")["metadata"]["uid"] != original_uid
    time.sleep(15)
    assert consistent("recreated"), "final Secret changed unexpectedly"
    owner, secret = get("stringsecret"), get("secret")
    root_ca = kubectl(
        "get", "configmap", "kube-root-ca.crt", "--ignore-not-found", "-o", "json"
    )
    return {
        "final_marker": "recreated",
        "final_consistency_checked": True,
        "original_cr_uid": original_uid,
        "final_cr_uid": owner["metadata"]["uid"],
        "final_secret_uid": secret["metadata"]["uid"],
        "secret_owner_uids": [
            ref["uid"] for ref in secret["metadata"].get("ownerReferences", [])
        ],
        "status_secret_uid": owner["status"]["secret"]["uid"],
        "kube_root_ca_configmap_present": bool(root_ca.strip()),
        "missed_state_intermediate_wait_skipped": allow_missed_intermediate_state,
        "missed_state_initial_wait_skipped": allow_missed_initial_state,
    }


if __name__ == "__main__":
    if sys.argv[1] != "create-update-recreate":
        raise SystemExit("unknown workload")
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        plan_path = output.parent / (output.parent.name + ".yaml")
        plan = yaml.safe_load(plan_path.read_text()) if plan_path.exists() else {}
        actions = plan.get("actions") or []
        allow_missed_intermediate_state = bool(actions) and (
            actions[0].get("actionType") == "pauseController"
            and actions[0].get("pauseAt") == "beforeControllerRead"
            and actions[0]["trigger"]["definitions"][0]["condition"]["conditionType"]
            != "onObjectDelete"
        )
        allow_missed_initial_state = allow_missed_intermediate_state and (
            actions[0]["trigger"]["definitions"][0]["condition"].get("conditionType")
            == "onObjectCreate"
            and actions[0]["trigger"]["definitions"][0]["condition"].get("resourceKey")
            == "stringsecret/default/" + NAME
            and actions[0]["trigger"]["definitions"][0]["condition"].get("occurrence")
            == 1
        )
        observations = run(allow_missed_intermediate_state, allow_missed_initial_state)
        output.with_name("workload-observations.json").write_text(
            json.dumps(observations, indent=2) + "\n"
        )
    except Exception:
        error = traceback.format_exc()
        output.write_text(error)
        print(error, file=sys.stderr)
        raise SystemExit(1)
    output.write_text("FINISH-SIEVE-TEST\n")
