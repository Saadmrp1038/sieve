"""Argo Rollouts blue-green checks use the public workload CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bluegreen_workload_requires_native_worker_counts_before_cluster_changes(
    tmp_path,
):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "assert sys.argv[1:] == ['--namespace','default','get','deployment',"
        "'argo-rollouts','--ignore-not-found','-o','json'], sys.argv\n"
        "print(json.dumps({'spec':{'template':{'spec':{'containers':["
        "{'name':'argo-rollouts','args':["
        "'--rollout-threads=4','--service-threads=10',"
        "'--experiment-threads=10','--analysis-threads=30',"
        "'--ingress-threads=10']}]} }}}))\n"
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/argo-rollouts/workloads/workload.py"),
            "bluegreen-post-analysis",
            str(output),
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(output.with_name("argo-rollouts-bluegreen.json").read_text())
    assert evidence["status"] == "configuration-failed"
    assert "--rollout-threads=10" in evidence["error"]
    assert not output.exists()


def test_bluegreen_workload_records_a_settled_rollback_failure(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["ARGO_ROLLOUTS_STATE"])
state = json.loads(state_path.read_text()) if state_path.exists() else {
    "patched": False,
    "sample": 0,
}
args = sys.argv[1:]
if args[:2] == ["--namespace", "default"]:
    args = args[2:]


def save():
    state_path.write_text(json.dumps(state))


def done(value):
    save()
    print(json.dumps(value))
    raise SystemExit(0)


def done_text(value):
    save()
    print(value)
    raise SystemExit(0)


def clean(arguments):
    result = []
    skip = False
    for item in arguments:
        if skip:
            skip = False
            continue
        if item in {"-o", "--output"}:
            skip = True
            continue
        if item == "--ignore-not-found":
            continue
        result.append(item)
    return result


def phase():
    if not state["patched"]:
        return "initial"
    return "promoted" if state["sample"] == 1 else "failed"


def rollout():
    current_phase = phase()
    initial = current_phase == "initial"
    failed = current_phase == "failed"
    active_hash = "hash-1" if initial else "hash-2"
    generation = 1 if initial else 2
    status = {
        "observedGeneration": str(generation),
        "phase": "Healthy" if initial else ("Degraded" if failed else "Progressing"),
        "stableRS": "hash-1",
        "currentPodHash": active_hash,
        "blueGreen": {"activeSelector": active_hash},
    }
    if failed:
        status["abort"] = False
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Rollout",
        "metadata": {"name": "sieve-bluegreen", "generation": generation},
        "status": status,
    }


def service(name):
    active_hash = "hash-1" if phase() == "initial" else "hash-2"
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name},
        "spec": {
            "selector": {
                "app": "sieve-bluegreen",
                "rollouts-pod-template-hash": active_hash,
            }
        },
    }


def resource_list(kind):
    if kind == "customresourcedefinitions":
        return {
            "items": [
                {"metadata": {"name": name}}
                for name in (
                    "rollouts.argoproj.io",
                    "analysisruns.argoproj.io",
                    "analysistemplates.argoproj.io",
                    "experiments.argoproj.io",
                )
            ]
        }
    if kind == "replicasets":
        return {
            "items": [
                {
                    "metadata": {
                        "labels": {"rollouts-pod-template-hash": pod_hash}
                    },
                    "spec": {
                        "template": {
                            "metadata": {"labels": {"sieve.io/revision": revision}}
                        }
                    },
                }
                for pod_hash, revision in (
                    ("hash-1", "revision-1"),
                    ("hash-2", "revision-2"),
                )
            ]
        }
    if kind == "pods" and "sievetag=argo-rollouts" in args:
        return {
            "items": [
                {
                    "metadata": {"name": "argo-rollouts-1", "uid": "controller-1"},
                    "status": {
                        "containerStatuses": [
                            {"name": "argo-rollouts", "restartCount": 0}
                        ]
                    },
                }
            ]
        }
    if kind == "pods":
        return {
            "items": [
                {
                    "metadata": {
                        "uid": uid,
                        "labels": {"sieve.io/revision": revision},
                    }
                }
                for uid, revision in (
                    ("pod-1", "revision-1"),
                    ("pod-2", "revision-2"),
                )
            ]
        }
    if kind == "analysisruns":
        status = "Failed" if phase() == "failed" else "Running"
        return {"items": [{"metadata": {"name": "analysis-1"}, "status": {"phase": status}}]}
    if kind == "jobs":
        return {"items": []}
    if kind == "endpointslices.discovery.k8s.io":
        uid = "pod-1" if phase() == "initial" else "pod-2"
        return {
            "items": [
                {
                    "endpoints": [
                        {
                            "conditions": {"ready": True},
                            "targetRef": {"uid": uid},
                        }
                    ]
                }
            ]
        }
    raise SystemExit("unexpected list kind: " + kind)


a = clean(args)
if a[:3] == ["get", "deployment", "argo-rollouts"]:
    done(
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "argo-rollouts",
                                "args": [
                                    "--rollout-threads=10",
                                    "--service-threads=10",
                                    "--experiment-threads=10",
                                    "--analysis-threads=30",
                                    "--ingress-threads=10",
                                ],
                            }
                        ]
                    }
                }
            }
        }
    )
if a[:2] == ["apply", "-f"]:
    done(json.load(sys.stdin))
if a[:3] == ["patch", "rollout", "sieve-bluegreen"]:
    state["patched"] = True
    state["sample"] = 0
    done(rollout())
if a[:3] == ["get", "rollout", "sieve-bluegreen"]:
    if state["patched"]:
        state["sample"] += 1
    done(rollout())
if a[:2] == ["get", "service"]:
    done(service(a[2]))
if a[:2] == ["get", "--raw"]:
    revision = "revision-1" if phase() == "initial" else "revision-2"
    done_text(revision)
if a[:2] == ["get", "customresourcedefinitions"]:
    done(resource_list("customresourcedefinitions"))
if a[:2] == ["get", "replicasets"]:
    done(resource_list("replicasets"))
if a[:2] == ["get", "pods"]:
    done(resource_list("pods"))
if a[:2] == ["get", "analysisruns"]:
    done(resource_list("analysisruns"))
if a[:2] == ["get", "jobs"]:
    done(resource_list("jobs"))
if a[:2] == ["get", "endpointslices.discovery.k8s.io"]:
    done(resource_list("endpointslices.discovery.k8s.io"))
raise SystemExit("unexpected kubectl command: " + repr(args))
"""
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/argo-rollouts/workloads/workload.py"),
            "bluegreen-post-analysis",
            str(output),
            "--timeout",
            "2.5",
            "--poll-seconds",
            "0.01",
            "--settle-seconds",
            "0.02",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "ARGO_ROLLOUTS_STATE": str(tmp_path / "state.json"),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("argo-rollouts-bluegreen.json").read_text())
    assert evidence["status"] == "completed"
    assert evidence["semantic_status"] == "failed"
    assert evidence["checks"]["post_promotion_analysis_failed"] is True
    assert evidence["checks"]["abort_preserved_the_original_stable_hash"] is False
    assert evidence["checks"]["active_service_and_status_returned_to_stable"] is False
    assert evidence["checks"]["live_traffic_returned_to_revision_one"] is False
    outcome = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert {item["name"]: item["passed"] for item in outcome["checks"]} == evidence[
        "checks"
    ]
