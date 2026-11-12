"""Flux helm-controller is exposed through public Sieve interfaces."""

import json
import os
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import yaml

from sieve_tool.common.config import load_controller_config

ROOT = Path(__file__).resolve().parents[1]


def load_workload_module():
    path = ROOT / "controllers/helm-controller/workloads/workload.py"
    spec = spec_from_file_location("helm_controller_upgrade_workload", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_profile_pins_release_reconcile_loop_and_ten_workers():
    profile = ROOT / "controllers/helm-controller"
    config = load_controller_config(profile)

    assert config.commit == "da037f220ee3e97eb91a70506b5c5f169e46bb68"
    assert config.client_go_version == "v0.37.1"
    assert config.annotated_reconcile_functions == {
        "internal/controller/helmrelease_controller.go": (
            "github.com/fluxcd/helm-controller/internal/controller."
            "(*HelmReleaseReconciler).Reconcile"
        )
    }

    deployment = yaml.safe_load((profile / "deploy/operator.yaml").read_text())
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["name"] == "manager"
    assert "--concurrent=10" in container["args"]
    assert "--watch-all-namespaces=false" in container["args"]

    build = (profile / "build/build.sh").read_text()
    assert "GOTOOLCHAIN=go1.27.0" in build
    assert "go build -trimpath -a" in build
    assert "main.go" in build


def test_failed_upgrade_workload_requires_ten_workers_before_cluster_changes(
    tmp_path,
):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "assert sys.argv[1:] == ['--namespace','default','get','deployment',"
        "'helm-controller','--ignore-not-found','-o','json'], sys.argv\n"
        "print(json.dumps({'spec':{'template':{'spec':{'containers':["
        "{'name':'manager','args':['--concurrent=4']}]} }}}))\n"
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/helm-controller/workloads/workload.py"),
            "failed-upgrade-remediation",
            str(output),
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(output.with_name("helm-upgrade.json").read_text())
    assert evidence["status"] == "configuration-failed"
    assert "--concurrent=10" in evidence["error"]
    assert not output.exists()


def test_timeout_records_the_last_observation_as_a_semantic_failure(tmp_path):
    workload = load_workload_module()
    initial = {
        "helmRelease": {"metadata": {"generation": 1}},
        "ready": {"status": "True", "observedGeneration": 1},
        "storage": [{"version": 1, "status": "deployed", "chartVersion": "0.1.0"}],
        "history": [{"version": 1, "chartVersion": "0.1.0"}],
        "deployment": {
            "metadata": {"generation": 1},
            "spec": {
                "template": {
                    "metadata": {"annotations": {"sieve.io/revision": "revision-1"}}
                }
            },
            "status": {
                "observedGeneration": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
            },
        },
        "configMap": {"data": {"index.html": "revision-1"}},
        "service": {"ok": True, "value": "revision-1"},
    }
    stalled = {
        "helmRelease": {
            "metadata": {"generation": 1},
            "status": {"conditions": [{"type": "Stalled", "status": "True"}]},
        },
        "ready": {"status": "False", "observedGeneration": 1},
        "storage": [
            {"version": 1, "status": "deployed", "chartVersion": "0.1.0"},
            {"version": 2, "status": "failed", "chartVersion": "0.2.0"},
            {"version": 3, "status": "failed", "chartVersion": "0.2.0"},
        ],
        "history": [{"version": 3, "status": "failed", "chartVersion": "0.2.0"}],
        "deployment": {
            "metadata": {"generation": 2},
            "spec": {
                "template": {
                    "metadata": {"annotations": {"sieve.io/revision": "revision-2"}}
                }
            },
            "status": {
                "observedGeneration": 2,
                "readyReplicas": 1,
                "updatedReplicas": 1,
            },
        },
        "configMap": {"data": {"index.html": "revision-2"}},
        "service": {"ok": True, "value": "revision-2"},
    }
    output = tmp_path / "workload.log"
    evidence = {
        "status": "upgrade-incomplete",
        "semantic_status": "not-evaluated",
        "initial": initial,
        "observations": [stalled],
    }

    workload.record_outcome(
        output, evidence, error="Timed out: failed upgrade rollback convergence"
    )

    semantic = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert semantic["checks"] == [
        {"name": "initial_status_storage_live_service_agree", "passed": True},
        {"name": "failed_upgrade_is_not_reported_ready", "passed": True},
        {"name": "rollback_storage_and_status_agree", "passed": False},
        {"name": "rollback_live_resources_and_service_agree", "passed": False},
        {"name": "durable_storage_revisions_are_in_status_history", "passed": False},
    ]
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    saved = json.loads(output.with_name("helm-upgrade.json").read_text())
    assert saved["final"] == stalled
    assert saved["semantic_status"] == "failed"
