"""Flux helm-controller is exposed through public Sieve interfaces."""

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from sieve_tool.common.config import load_controller_config

ROOT = Path(__file__).resolve().parents[1]


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
