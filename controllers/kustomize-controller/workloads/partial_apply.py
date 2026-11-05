"""Observe inventory and cleanup after successful or partially rejected apply."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import tarfile
import time
import traceback
from pathlib import Path

import yaml
from workload import apply, patch

from sieve_tool.paths import repository_root
from sieve_tool.process import execution, run
from sieve_tool.testing.child_repair import get, kubectl, ready

PARENT = "sieve-partial"
NAMESPACE = "sieve-partial-created"
ANCHOR = "sieve-partial-anchor"
BLOCKED = "sieve-partial-blocked"
SOURCE = "sieve-partial-source"
POLICY = "sieve-partial-deny"
OWNER = "kustomize.toolkit.fluxcd.io/"


def archive(resources):
    files = {
        "kustomization.yaml": yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["resources.yaml"],
            }
        ),
        "resources.yaml": yaml.safe_dump_all(resources),
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, text in files.items():
            body = text.encode()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(body), 0o644, 0
            tar.addfile(entry, io.BytesIO(body))
    return gzip.compress(stream.getvalue(), mtime=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workload", choices=["partial-apply-control", "partial-apply-rejected"]
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--settle-seconds", type=float, default=5)
    args = parser.parse_args()
    for value in (args.timeout, args.settle_seconds):
        if not math.isfinite(value) or value <= 0:
            parser.error("timeouts and stability durations must be finite and positive")
    rejected = args.workload == "partial-apply-rejected"
    out = args.output.parent
    out.mkdir(parents=True, exist_ok=True)
    args.output.write_text("")
    evidence = {
        "status": "configuration_failed",
        "workload": args.workload,
        "workers": 10,
        "scheduling_faults": 0,
        "accepted": [],
        "observations": [],
        "cleanup_reconciliations": [],
    }

    def save():
        (out / "partial-application.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def accept(value):
        evidence["accepted"].append({"time_ns": time.time_ns(), "resource": value})
        save()
        return value

    def sample(phase):
        row = {
            "phase": phase,
            "time_ns": time.time_ns(),
            "parent": get("kustomization", PARENT),
            "namespace": get("namespace", NAMESPACE),
            "anchor": get("configmap", ANCHOR),
            "blocked": get("configmap", BLOCKED),
            "pods": json.loads(
                kubectl(
                    "get", "pods", "-l", "sievetag=kustomize-controller", "-o", "json"
                )
            ),
        }
        evidence["observations"].append(row)
        save()
        return row

    def wait(check, description, *, stable=False):
        end = time.monotonic() + args.timeout
        since = None
        while True:
            ok = check()
            now = time.monotonic()
            since = (since if since is not None else now) if ok else None
            if ok and (not stable or now - since >= args.settle_seconds):
                return
            if now >= end:
                raise RuntimeError("Timed out: " + description)
            time.sleep(0.25)

    def inventory(parent):
        return {
            e["id"]
            for e in parent.get("status", {}).get("inventory", {}).get("entries", [])
        }

    def current(parent, revision):
        return ready(parent) and parent["status"].get("lastAppliedRevision") == revision

    try:
        with execution({"request": 35}, out / "partial-commands"):
            deployment = get("deployment", "kustomize-controller")
            evidence["deployment"] = deployment
            manager = next(
                c
                for c in deployment["spec"]["template"]["spec"]["containers"]
                if c["name"] == "manager"
            )
            if [a for a in manager["args"] if a.startswith("--concurrent")] != [
                "--concurrent=10"
            ]:
                raise RuntimeError("Experiment requires native --concurrent=10")
            definitions = json.loads(
                kubectl("get", "customresourcedefinitions", "-o", "json")
            )
            required = {"kustomizations.kustomize.toolkit.fluxcd.io"} | {
                k + ".source.toolkit.fluxcd.io"
                for k in (
                    "gitrepositories",
                    "ocirepositories",
                    "buckets",
                    "externalartifacts",
                )
            }
            if missing := required - {
                x["metadata"]["name"] for x in definitions["items"]
            }:
                raise RuntimeError(
                    "Missing native watched APIs: " + repr(sorted(missing))
                )
            if get("namespace", NAMESPACE) is not None:
                raise RuntimeError("Synthetic namespace already exists")
            evidence["status"] = "setup_failed"
            anchor = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": ANCHOR, "namespace": "default"},
                "data": {"value": "synthetic-anchor"},
            }
            blocked = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": BLOCKED, "namespace": "default"},
                "data": {"value": "synthetic-rejected"},
            }
            namespace = {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": NAMESPACE},
            }
            data = {
                "initial.tgz": archive([anchor]),
                "expanded.tgz": archive([anchor, namespace, blocked]),
                "removed.tgz": archive([anchor]),
            }
            evidence["artifacts"] = {}
            for name, body in data.items():
                (out / name).write_bytes(body)
                evidence["artifacts"][name] = hashlib.sha256(body).hexdigest()
            accept(
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": SOURCE},
                        "binaryData": {
                            n: base64.b64encode(b).decode() for n, b in data.items()
                        },
                    }
                )
            )
            accept(
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "Service",
                        "metadata": {"name": SOURCE},
                        "spec": {
                            "selector": {"app": SOURCE},
                            "ports": [{"port": 8080, "targetPort": 8080}],
                        },
                    }
                )
            )
            accept(
                apply(
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "metadata": {"name": SOURCE},
                        "spec": {
                            "replicas": 1,
                            "selector": {"matchLabels": {"app": SOURCE}},
                            "template": {
                                "metadata": {"labels": {"app": SOURCE}},
                                "spec": {
                                    "containers": [
                                        {
                                            "name": "http",
                                            "image": "busybox:1.37.0",
                                            "command": [
                                                "httpd",
                                                "-f",
                                                "-p",
                                                "8080",
                                                "-h",
                                                "/www",
                                            ],
                                            "volumeMounts": [
                                                {"name": "data", "mountPath": "/www"}
                                            ],
                                        }
                                    ],
                                    "volumes": [
                                        {"name": "data", "configMap": {"name": SOURCE}}
                                    ],
                                },
                            },
                        },
                    }
                )
            )
            kubectl("rollout", "status", "deployment/" + SOURCE, "--timeout=120s")
            source = accept(
                apply(
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "GitRepository",
                        "metadata": {"name": PARENT},
                        "spec": {
                            "interval": "1h",
                            "url": "https://synthetic.invalid/disposable.git",
                        },
                    }
                )
            )

            def publish(name, digit):
                revision = "main@sha1:" + digit * 40
                accept(
                    patch(
                        "gitrepository",
                        PARENT,
                        {
                            "status": {
                                "observedGeneration": source["metadata"]["generation"],
                                "conditions": [
                                    {
                                        "type": "Ready",
                                        "status": "True",
                                        "reason": "Succeeded",
                                        "message": "synthetic source fixture",
                                        "lastTransitionTime": "2026-11-03T00:00:00Z",
                                        "observedGeneration": source["metadata"][
                                            "generation"
                                        ],
                                    }
                                ],
                                "artifact": {
                                    "path": "synthetic/" + name,
                                    "url": "http://"
                                    + SOURCE
                                    + ".default.svc.cluster.local:8080/"
                                    + name,
                                    "revision": revision,
                                    "digest": "sha256:" + evidence["artifacts"][name],
                                    "lastUpdateTime": "2026-11-03T00:00:00Z",
                                    "size": len(data[name]),
                                },
                            }
                        },
                        status=True,
                    )
                )
                return revision

            initial_rev = publish("initial.tgz", "1")
            accept(
                apply(
                    {
                        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
                        "kind": "Kustomization",
                        "metadata": {"name": PARENT},
                        "spec": {
                            "interval": "1h",
                            "retryInterval": "2s",
                            "timeout": "30s",
                            "path": "./",
                            "prune": True,
                            "deletionPolicy": "Delete",
                            "sourceRef": {"kind": "GitRepository", "name": PARENT},
                        },
                    }
                )
            )
            wait(
                lambda: current(sample("initial")["parent"], initial_rev),
                "initial acknowledged revision",
                stable=True,
            )
            evidence["initial"] = sample("initial-established")
            if inventory(evidence["initial"]["parent"]) != {
                "default_" + ANCHOR + "__ConfigMap"
            }:
                raise RuntimeError(
                    "Initial inventory does not contain exactly the anchor"
                )
            accept(
                apply(
                    {
                        "apiVersion": "admissionregistration.k8s.io/v1",
                        "kind": "ValidatingAdmissionPolicy",
                        "metadata": {"name": POLICY},
                        "spec": {
                            "failurePolicy": "Fail",
                            "matchConstraints": {
                                "resourceRules": [
                                    {
                                        "apiGroups": [""],
                                        "apiVersions": ["v1"],
                                        "operations": ["CREATE", "UPDATE"],
                                        "resources": ["configmaps"],
                                    }
                                ]
                            },
                            "matchConditions": [
                                {
                                    "name": "blocked",
                                    "expression": "request.namespace == 'default' && request.name == '"
                                    + BLOCKED
                                    + "'",
                                }
                            ],
                            "validations": [
                                {
                                    "expression": "false",
                                    "message": "sieve synthetic apply deny",
                                }
                            ],
                        },
                    }
                )
            )
            accept(
                apply(
                    {
                        "apiVersion": "admissionregistration.k8s.io/v1",
                        "kind": "ValidatingAdmissionPolicyBinding",
                        "metadata": {"name": POLICY},
                        "spec": {
                            "policyName": POLICY,
                            "validationActions": ["Deny" if rejected else "Audit"],
                        },
                    }
                )
            )

            def probe():
                receipt = run(
                    [
                        "kubectl",
                        "--namespace",
                        "default",
                        "create",
                        "--dry-run=server",
                        "-f",
                        "-",
                        "-o",
                        "json",
                    ],
                    input=json.dumps(blocked),
                    cwd=repository_root(),
                    capture_output=True,
                    check=False,
                    category="request",
                    operation="independent admission probe",
                )
                evidence["admission_probe"] = {
                    "returncode": receipt.returncode,
                    "stdout": receipt.stdout,
                    "stderr": receipt.stderr,
                }
                save()
                return (
                    (
                        receipt.returncode != 0
                        and "sieve synthetic apply deny" in receipt.stderr
                    )
                    if rejected
                    else receipt.returncode == 0
                )

            wait(probe, "admission behavior established")
            expanded_rev = publish("expanded.tgz", "2")
            evidence["status"] = "observing"

            def expanded():
                row = sample("expanded")
                parent, ns = row["parent"], row["namespace"]
                if parent is None or ns is None:
                    return False
                if ns["metadata"].get("labels", {}).get(OWNER + "name") != PARENT or ns[
                    "metadata"
                ].get("deletionTimestamp"):
                    raise RuntimeError("Namespace creation/ownership not established")
                if not rejected:
                    return current(parent, expanded_rev) and row["blocked"] is not None
                status = parent.get("status", {})
                return (
                    status.get("lastAttemptedRevision") == expanded_rev
                    and any(
                        c.get("type") == "Ready"
                        and c.get("status") == "False"
                        and c.get("observedGeneration")
                        == parent["metadata"]["generation"]
                        and c.get("reason") == "ReconciliationFailed"
                        and "sieve synthetic apply deny" in c.get("message", "")
                        for c in status.get("conditions", [])
                    )
                    and row["blocked"] is None
                )

            wait(expanded, "expanded revision with namespace creation", stable=True)
            evidence["expanded"] = sample("expanded-established")
            checks = {
                "created_namespace_tracked_after_apply": "_" + NAMESPACE + "__Namespace"
                in inventory(evidence["expanded"]["parent"])
            }
            # Acknowledge the replacement revision before removing the denial:
            # an intervening successful retry of revision 2 would repair inventory.
            removed_rev = publish("removed.tgz", "3")
            wait(
                lambda: current(sample("removed")["parent"], removed_rev),
                "source-removal revision acknowledged",
                stable=True,
            )
            kubectl("delete", "validatingadmissionpolicybinding", POLICY)
            kubectl("delete", "validatingadmissionpolicy", POLICY)
            for i in range(3):
                token = "sieve-partial-cleanup-" + str(i)
                accept(
                    patch(
                        "kustomization",
                        PARENT,
                        {
                            "metadata": {
                                "annotations": {
                                    "reconcile.fluxcd.io/requestedAt": token
                                }
                            }
                        },
                    )
                )
                wait(
                    lambda t=token: (
                        current(
                            (p := sample("cleanup-reconcile")["parent"]), removed_rev
                        )
                        and p["status"].get("lastHandledReconcileAt") == t
                    ),
                    "cleanup reconciliation " + token,
                    stable=True,
                )
                evidence["cleanup_reconciliations"].append(
                    sample("cleanup-acknowledged")
                )
            evidence["removed"] = sample("removed-established")
            checks["namespace_pruned_after_source_removal"] = (
                evidence["removed"]["namespace"] is None
            )
            kubectl("delete", "kustomization", PARENT, "--wait=false", "-o", "name")
            wait(
                lambda: sample("finalizing")["parent"] is None,
                "parent finalization",
                stable=True,
            )
            evidence["final"] = sample("final-established")
            checks["namespace_absent_after_parent_finalization"] = (
                evidence["final"]["namespace"] is None
            )
            evidence.update(
                status="completed",
                semantic_status="passed" if all(checks.values()) else "failed",
                checks=checks,
            )
            (out / "semantic-outcome.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checks": [
                            {"name": name, "passed": passed}
                            for name, passed in checks.items()
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )
            args.output.write_text("FINISH-SIEVE-TEST\n")
    except Exception as error:
        evidence.update(error=str(error), traceback=traceback.format_exc())
        save()
        return 1
    save()
    return 0
