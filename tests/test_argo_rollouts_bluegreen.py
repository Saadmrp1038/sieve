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
