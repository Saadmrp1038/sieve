#!/usr/bin/env python3
"""Draft standalone reproducer. Prepared from sealed inputs; not executed."""

import argparse
import base64
import copy
import hashlib
import json
import os
import subprocess
import time
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context", required=True, help="Disposable Kubernetes context"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Fresh evidence path"
    )
    parser.add_argument(
        "--control", action="store_true", help="Use Audit instead of Deny"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    bundle = Path(__file__).resolve().parent
    objects = json.loads((bundle / "objects.json").read_text())
    expected = json.loads((bundle / "observed-results.json").read_text())["cases"][
        "rejected-1"
    ]["artifact_sha256"]
    env = {
        k: v
        for k, v in os.environ.items()
        if "API_KEY" not in k
        and not k.startswith(("OPENAI_", "CODEX_API_", "DECISIONS_"))
    }
    evidence = {
        "status": "setup_failed",
        "control": args.control,
        "commands": [],
        "samples": [],
    }

    def save():
        (args.output / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")

    def command(*argv, payload=None, check=True):
        full = ["kubectl", "--context", args.context, "--namespace", "default", *argv]
        result = subprocess.run(
            full,
            input=json.dumps(payload) if payload is not None else None,
            text=True,
            capture_output=True,
            timeout=40,
            env=env,
        )
        evidence["commands"].append(
            {
                "argv": full,
                "payload": payload,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        save()
        if check and result.returncode:
            raise RuntimeError("kubectl failed: " + result.stderr)
        return result

    def get(kind, name):
        raw = command("get", kind, name, "--ignore-not-found", "-o", "json").stdout
        return json.loads(raw) if raw.strip() else None

    def apply(key):
        return json.loads(
            command("apply", "-f", "-", "-o", "json", payload=objects[key]).stdout
        )

    def sample(phase):
        row = {
            "phase": phase,
            "time_ns": time.time_ns(),
            "parent": get("kustomization", "sieve-partial"),
            "namespace": get("namespace", "sieve-partial-created"),
            "anchor": get("configmap", "sieve-partial-anchor"),
            "blocked": get("configmap", "sieve-partial-blocked"),
        }
        evidence["samples"].append(row)
        save()
        return row

    def wait(predicate, description, stable=5):
        end, since = time.monotonic() + 90, None
        while True:
            ok, now = predicate(), time.monotonic()
            since = (since if since is not None else now) if ok else None
            if ok and now - since >= stable:
                return
            if now >= end:
                raise RuntimeError("Timed out: " + description)
            time.sleep(0.25)

    def revision(n):
        return "main@sha1:" + str(n) * 40

    def ready(parent, n):
        return (
            parent is not None
            and parent.get("status", {}).get("lastAppliedRevision") == revision(n)
            and any(
                c.get("type") == "Ready"
                and c.get("status") == "True"
                and c.get("observedGeneration") == parent["metadata"]["generation"]
                for c in parent["status"].get("conditions", [])
            )
        )

    def inventory(parent):
        return {
            e["id"]
            for e in parent.get("status", {}).get("inventory", {}).get("entries", [])
        }

    try:
        for name, digest in expected.items():
            body = (bundle / "artifacts" / name).read_bytes()
            if hashlib.sha256(body).hexdigest() != digest:
                raise RuntimeError("Archive checksum mismatch: " + name)
        deployment = get("deployment", "kustomize-controller")
        evidence["deployment"] = deployment
        containers = deployment["spec"]["template"]["spec"]["containers"]
        manager = next(c for c in containers if c["name"] == "manager")
        if [a for a in manager["args"] if a.startswith("--concurrent")] != [
            "--concurrent=10"
        ]:
            raise RuntimeError("Requires native --concurrent=10")
        # Fixed synthetic names must be unused; the script never resets an old run.
        for obj in objects.values():
            if get(obj["kind"].lower(), obj["metadata"]["name"]) is not None:
                raise RuntimeError(
                    "Synthetic input already exists: "
                    + obj["kind"]
                    + "/"
                    + obj["metadata"]["name"]
                )
        for kind, name in [
            ("namespace", "sieve-partial-created"),
            ("configmap", "sieve-partial-anchor"),
            ("configmap", "sieve-partial-blocked"),
        ]:
            if get(kind, name) is not None:
                raise RuntimeError("Synthetic resource already exists: " + name)
        objects["ConfigMap/sieve-partial-source"]["binaryData"] = {
            n: base64.b64encode((bundle / "artifacts" / n).read_bytes()).decode()
            for n in expected
        }
        for key in (
            "ConfigMap/sieve-partial-source",
            "Service/sieve-partial-source",
            "Deployment/sieve-partial-source",
        ):
            apply(key)
        command("rollout", "status", "deployment/sieve-partial-source", "--timeout=30s")
        source = apply("GitRepository/sieve-partial")

        def publish(name, number):
            # Synthetic source fixture, not a Git checkout. source-controller must
            # be absent so it cannot replace this status with a download failure.
            status = {
                "observedGeneration": source["metadata"]["generation"],
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "True",
                        "reason": "Succeeded",
                        "message": "synthetic source fixture",
                        "lastTransitionTime": "2026-11-03T00:00:00Z",
                        "observedGeneration": source["metadata"]["generation"],
                    }
                ],
                "artifact": {
                    "path": "synthetic/" + name,
                    "url": "http://sieve-partial-source.default.svc.cluster.local:8080/"
                    + name,
                    "revision": revision(number),
                    "digest": "sha256:" + expected[name],
                    "lastUpdateTime": "2026-11-03T00:00:00Z",
                    "size": (bundle / "artifacts" / name).stat().st_size,
                },
            }
            command(
                "patch",
                "gitrepository",
                "sieve-partial",
                "--subresource=status",
                "--type=merge",
                "-p",
                json.dumps({"status": status}),
                "-o",
                "json",
            )

        publish("initial.tgz", 1)
        apply("Kustomization/sieve-partial")
        wait(lambda: ready(sample("initial")["parent"], 1), "initial revision")
        initial = sample("initial-established")
        if inventory(initial["parent"]) != {"default_sieve-partial-anchor__ConfigMap"}:
            raise RuntimeError("Initial inventory not established")
        apply("ValidatingAdmissionPolicy/sieve-partial-deny")
        binding = objects["ValidatingAdmissionPolicyBinding/sieve-partial-deny"]
        binding["spec"]["validationActions"] = ["Audit" if args.control else "Deny"]
        apply("ValidatingAdmissionPolicyBinding/sieve-partial-deny")
        probe = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "sieve-partial-blocked", "namespace": "default"},
            "data": {"value": "synthetic-rejected"},
        }

        def admission():
            r = command(
                "create",
                "--dry-run=server",
                "-f",
                "-",
                "-o",
                "json",
                payload=probe,
                check=False,
            )
            return (
                r.returncode == 0
                if args.control
                else r.returncode != 0 and "sieve synthetic apply deny" in r.stderr
            )

        wait(admission, "admission policy", stable=0)
        publish("expanded.tgz", 2)

        def expanded():
            row = sample("expanded")
            parent, ns = row["parent"], row["namespace"]
            if parent is None or ns is None:
                return False
            if args.control:
                return ready(parent, 2) and row["blocked"] is not None
            return (
                parent["status"].get("lastAttemptedRevision") == revision(2)
                and row["blocked"] is None
                and any(
                    c.get("type") == "Ready"
                    and c.get("status") == "False"
                    and c.get("reason") == "ReconciliationFailed"
                    and c.get("observedGeneration") == parent["metadata"]["generation"]
                    and "sieve synthetic apply deny" in c.get("message", "")
                    for c in parent["status"].get("conditions", [])
                )
            )

        wait(expanded, "Namespace created before later-stage rejection")
        expanded_row = copy.deepcopy(evidence["samples"][-1])
        publish("removed.tgz", 3)
        wait(lambda: ready(sample("removed")["parent"], 3), "removal revision")
        for kind in ("validatingadmissionpolicybinding", "validatingadmissionpolicy"):
            command("delete", kind, "sieve-partial-deny")
        for i in range(3):
            token = "sieve-partial-cleanup-" + str(i)
            command(
                "annotate",
                "kustomization",
                "sieve-partial",
                "reconcile.fluxcd.io/requestedAt=" + token,
                "--overwrite",
            )
            wait(
                lambda t=token: (
                    ready((p := sample("cleanup")["parent"]), 3)
                    and p["status"].get("lastHandledReconcileAt") == t
                ),
                "cleanup request " + token,
            )
        removed = sample("removed-established")
        command(
            "delete", "kustomization", "sieve-partial", "--wait=false", "-o", "name"
        )
        wait(lambda: sample("finalizing")["parent"] is None, "parent finalization")
        final = sample("final")
        checks = {
            "created_namespace_tracked": "_sieve-partial-created__Namespace"
            in inventory(expanded_row["parent"]),
            "pruned_after_source_removal": removed["namespace"] is None,
            "absent_after_parent_finalization": final["namespace"] is None,
        }
        same_live_ns = (
            final["namespace"] is not None
            and final["namespace"]["metadata"]["uid"]
            == expanded_row["namespace"]["metadata"]["uid"]
            and not final["namespace"]["metadata"].get("deletionTimestamp")
        )
        reproduced = not args.control and not any(checks.values()) and same_live_ns
        evidence.update(status="completed", checks=checks, reproduced=reproduced)
        command("logs", "deployment/kustomize-controller", "--all-containers=true")
        save()
        print(
            json.dumps(
                {
                    "reproduced": reproduced,
                    "checks": checks,
                    "output": str(args.output),
                },
                indent=2,
            )
        )
        return 42 if reproduced else 0 if all(checks.values()) else 2
    except Exception:
        evidence["error"] = traceback.format_exc()
        save()
        raise


if __name__ == "__main__":
    raise SystemExit(main())
