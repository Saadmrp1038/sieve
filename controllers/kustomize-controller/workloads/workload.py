"""Synthetic source, rejected prune, retained inventory, and policy controls."""

import argparse
import base64
import gzip
import hashlib
import io
import json
import math
import sys
import tarfile
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import yaml

from sieve_tool.paths import repository_root
from sieve_tool.process import execution, run
from sieve_tool.testing.child_repair import get, kubectl, ready
from sieve_tool.testing.pruning import PruneRecoveryMonitor
from sieve_tool.testing.semantic import StabilityWindow

VICTIM = "zz-sieve-prune-victim"
DISABLED = "yy-sieve-prune-disabled"
FOREIGN = "xx-sieve-prune-foreign"
ANCHOR = "sieve-prune-anchor"
PARENT = "sieve-prune"
POLICY = "sieve-prune-deny"
OWNER = "kustomize.toolkit.fluxcd.io/"
REVISION = "main@sha1:" + "2" * 40


def apply(value):
    return json.loads(kubectl("apply", "-f", "-", "-o", "json", payload=value))


def patch(kind, name, value, *, status=False):
    return json.loads(
        kubectl(
            "patch",
            kind,
            name,
            "--type=merge",
            "-p",
            json.dumps(value),
            *(["--subresource=status"] if status else []),
            "-o",
            "json",
        )
    )


def artifact(names):
    content = {
        "kustomization.yaml": yaml.safe_dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "resources": ["resources.yaml"],
            }
        ),
        "resources.yaml": yaml.safe_dump_all(
            [
                {
                    "apiVersion": "v1",
                    "kind": "ConfigMap",
                    "metadata": {
                        "name": name,
                        "namespace": "default",
                        **(
                            {"annotations": {OWNER + "prune": "disabled"}}
                            if name == DISABLED
                            else {}
                        ),
                    },
                    "data": {"value": "synthetic-" + name},
                }
                for name in names
            ]
        ),
    }
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as archive:
        for name, text in content.items():
            body = text.encode()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(body), 0o644, 0
            archive.addfile(entry, io.BytesIO(body))
    return gzip.compress(data.getvalue(), mtime=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["prune-retry"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be finite and positive")
    directory = args.output.parent
    directory.mkdir(parents=True, exist_ok=True)
    args.output.write_text("")
    evidence = {
        "status": "configuration_failed",
        "workers": 10,
        "observations": [],
        "accepted": [],
        "artifacts": {},
    }

    def save():
        directory.joinpath("prune-observations.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )

    def accept(value):
        evidence["accepted"].append(value)
        save()
        return value

    def wait(check, description):
        end = time.monotonic() + args.timeout
        while True:
            if check():
                return
            if time.monotonic() >= end:
                raise RuntimeError("Timed out: " + description)
            time.sleep(0.5)

    try:
        with execution({"request": 35}, directory / "prune-commands"):
            deployment = get("deployment", "kustomize-controller")
            evidence["deployment"] = deployment
            containers = deployment["spec"]["template"]["spec"]["containers"]
            manager = next(c for c in containers if c["name"] == "manager")
            worker_args = [a for a in manager["args"] if a.startswith("--concurrent")]
            if worker_args != ["--concurrent=10"]:
                raise RuntimeError("Experiment requires native --concurrent=10")
            definitions = json.loads(
                kubectl("get", "customresourcedefinitions", "-o", "json")
            )
            installed = {item["metadata"]["name"] for item in definitions["items"]}
            required = {"kustomizations.kustomize.toolkit.fluxcd.io"} | {
                kind + ".source.toolkit.fluxcd.io"
                for kind in (
                    "gitrepositories",
                    "ocirepositories",
                    "buckets",
                    "externalartifacts",
                )
            }
            if missing := required - installed:
                raise RuntimeError(
                    "Missing native watched APIs: " + ", ".join(sorted(missing))
                )
            evidence["status"] = "setup_failed"
            data = {
                "initial.tgz": artifact([VICTIM, DISABLED, FOREIGN, ANCHOR]),
                "pruned.tgz": artifact([ANCHOR]),
            }
            for name, body in data.items():
                directory.joinpath(name).write_bytes(body)
                evidence["artifacts"][name] = hashlib.sha256(body).hexdigest()
            accept(
                apply(
                    {
                        "apiVersion": "v1",
                        "kind": "ConfigMap",
                        "metadata": {"name": "sieve-prune-source"},
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
                        "metadata": {"name": "sieve-prune-source"},
                        "spec": {
                            "selector": {"app": "sieve-prune-source"},
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
                        "metadata": {"name": "sieve-prune-source"},
                        "spec": {
                            "replicas": 1,
                            "selector": {"matchLabels": {"app": "sieve-prune-source"}},
                            "template": {
                                "metadata": {"labels": {"app": "sieve-prune-source"}},
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
                                        {
                                            "name": "data",
                                            "configMap": {"name": "sieve-prune-source"},
                                        }
                                    ],
                                },
                            },
                        },
                    }
                )
            )
            kubectl(
                "rollout", "status", "deployment/sieve-prune-source", "--timeout=120s"
            )
            source = accept(
                apply(
                    {
                        "apiVersion": "source.toolkit.fluxcd.io/v1",
                        "kind": "GitRepository",
                        "metadata": {"name": PARENT},
                        "spec": {
                            "interval": "1h",
                            "url": "https://example.invalid/synthetic",
                        },
                    }
                )
            )

            def publish(name, revision):
                return accept(
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
                                        "lastTransitionTime": "2026-11-02T00:00:00Z",
                                        "observedGeneration": source["metadata"][
                                            "generation"
                                        ],
                                    }
                                ],
                                "artifact": {
                                    "path": "synthetic/" + name,
                                    "url": "http://sieve-prune-source.default.svc.cluster.local:8080/"
                                    + name,
                                    "revision": revision,
                                    "digest": "sha256:" + evidence["artifacts"][name],
                                    "lastUpdateTime": "2026-11-02T00:00:00Z",
                                    "size": len(data[name]),
                                },
                            }
                        },
                        status=True,
                    )
                )

            publish("initial.tgz", "main@sha1:" + "1" * 40)
            accepted = accept(
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
                            "sourceRef": {"kind": "GitRepository", "name": PARENT},
                        },
                    }
                )
            )
            wait(lambda: ready(get("kustomization", PARENT)), "initial ready")
            initial = {
                n: get("configmap", n) for n in (VICTIM, DISABLED, FOREIGN, ANCHOR)
            }
            if any(
                v is None
                or v["metadata"].get("labels", {}).get(OWNER + "name") != PARENT
                for v in initial.values()
            ):
                raise RuntimeError("Initial owned synthetic resources not established")
            evidence["initial"] = initial
            evidence["initial_parent"] = get("kustomization", PARENT)
            foreign = accept(
                patch(
                    "configmap",
                    FOREIGN,
                    {"metadata": {"labels": {OWNER + "name": "synthetic-successor"}}},
                )
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
                                        "operations": ["DELETE"],
                                        "resources": ["configmaps"],
                                    }
                                ]
                            },
                            "matchConditions": [
                                {
                                    "name": "victim",
                                    "expression": f"request.namespace == 'default' && request.name == '{VICTIM}'",
                                }
                            ],
                            "validations": [
                                {
                                    "expression": "false",
                                    "message": "sieve synthetic prune deny",
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
                        "spec": {"policyName": POLICY, "validationActions": ["Deny"]},
                    }
                )
            )

            def rejecting():
                receipt = run(
                    [
                        "kubectl",
                        "-n",
                        "default",
                        "delete",
                        "configmap",
                        VICTIM,
                        "--dry-run=server",
                        "--wait=false",
                        "-o",
                        "name",
                    ],
                    cwd=repository_root(),
                    capture_output=True,
                    check=False,
                    operation="independent dry-run delete policy check",
                    category="request",
                )
                evidence["denial_probe"] = {
                    "returncode": receipt.returncode,
                    "stdout": receipt.stdout,
                    "stderr": receipt.stderr,
                }
                return (
                    receipt.returncode != 0
                    and "sieve synthetic prune deny" in receipt.stderr
                )

            wait(rejecting, "admission denial established by server dry-run")
            evidence["status"] = "observing"
            publish("pruned.tgz", REVISION)
            monitor = PruneRecoveryMonitor(
                accepted=accepted,
                victim=initial[VICTIM],
                controls={
                    DISABLED: initial[DISABLED],
                    FOREIGN: foreign,
                    ANCHOR: initial[ANCHOR],
                },
                inventory_id="default_" + VICTIM + "__ConfigMap",
                revision=REVISION,
            )

            def observe(phase, window):
                parent = get("kustomization", PARENT)
                victim = get("configmap", VICTIM)
                controls = {n: get("configmap", n) for n in (DISABLED, FOREIGN, ANCHOR)}
                pods = json.loads(
                    kubectl(
                        "get",
                        "pods",
                        "-l",
                        "sievetag=kustomize-controller",
                        "-o",
                        "json",
                    )
                )
                verdict = monitor.observe(
                    phase=phase, parent=parent, victim=victim, controls=controls
                )
                row = {
                    "phase": phase,
                    "time_ns": time.time_ns(),
                    "parent": parent,
                    "victim": victim,
                    "controls": controls,
                    "pods": pods,
                    "verdict": asdict(verdict),
                }
                evidence["observations"].append(row)
                save()
                if verdict.status == "violation":
                    evidence["status"] = "semantic_violation"
                    raise RuntimeError("Semantic violation: " + repr(verdict.reasons))
                epoch = ",".join(
                    sorted(
                        p["metadata"]["uid"]
                        for p in pods["items"]
                        if not p["metadata"].get("deletionTimestamp")
                    )
                )
                return window.observe(verdict, now_ns=time.monotonic_ns(), epoch=epoch)

            for phase in ("blocked", "recovered"):
                window = StabilityWindow(seconds=3)
                wait(
                    lambda p=phase, w=window: observe(p, w),
                    phase + " semantic stability",
                )
                if phase == "blocked":
                    accept(get("validatingadmissionpolicy", POLICY))
                    kubectl("delete", "validatingadmissionpolicybinding", POLICY)
                    kubectl("delete", "validatingadmissionpolicy", POLICY)
                    # No explicit reconcile request: retryInterval must recover naturally.
            evidence["status"] = "passed"
            directory.joinpath("semantic-outcome.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "checks": [
                            {"name": name, "passed": True}
                            for name in (
                                "rejected_prune_retains_inventory",
                                "prune_recovers_after_denial_removed",
                                "prune_policy_controls_preserved",
                            )
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


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in (
        "partial-apply-control",
        "partial-apply-rejected",
    ):
        from partial_apply import main as partial_main

        raise SystemExit(partial_main())
    raise SystemExit(main())
