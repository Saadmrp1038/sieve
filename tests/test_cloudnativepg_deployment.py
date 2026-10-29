"""Published operands enter the node through the same CRI used by kubelet."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("bad_identity", [False, True])
def test_deployment_resolves_digest_images_by_cri_config_id(tmp_path, bad_identity):
    deployment = tmp_path / "operator.yaml"
    deployment.write_text(
        (ROOT / "controllers/cloudnative-pg/deploy/operator.yaml")
        .read_text()
        .replace("${SIEVE-DR}", "local")
        .replace("${SIEVE-DT}", "vanilla")
    )
    state = tmp_path / "calls.json"
    state.write_text("[]")
    commands = {
        "kind": "import sys\nassert sys.argv[1:3]==['load','docker-image']\n",
        "docker": """import json,os,sys
from pathlib import Path
p=Path(os.environ['CRI_TEST_CALLS']);calls=json.loads(p.read_text());a=sys.argv[1:];calls.append(a);p.write_text(json.dumps(calls))
assert a[:3]==['exec','kind-control-plane','crictl']
if a[3]=='pull':print('Image is up to date for sha256:config')
elif a[3]=='inspecti':
 if a[-1]=='sha256:config' and os.environ['CRI_TEST_BAD_ID']=='1':raise SystemExit('config identity not found')
 print(json.dumps({'status':{'id':'sha256:config'}}))
else:raise SystemExit('unexpected CRI command')
""",
        # Stop after preloading: the installation checksum/apply path has its
        # own pinned release, and this test never contacts an external service.
        "curl": "raise SystemExit(73)\n",
    }
    for name, body in commands.items():
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o755)
    images = {
        "SIEVE_CNPG_POSTGRES_IMAGE": "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie@sha256:"
        + "1" * 64,
        "SIEVE_CNPG_PGBOUNCER_IMAGE": "ghcr.io/cloudnative-pg/pgbouncer:1.25.1@sha256:"
        + "2" * 64,
    }
    result = subprocess.run(
        ["/bin/sh", str(ROOT / "controllers/cloudnative-pg/deploy/deploy.sh")],
        cwd=tmp_path,
        env={
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(ROOT / "src"),
            "CRI_TEST_CALLS": str(state),
            "CRI_TEST_BAD_ID": "1" if bad_identity else "0",
            **images,
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    calls = json.loads(state.read_text())
    if bad_identity:
        assert result.returncode not in (0, 73)
        assert not (tmp_path / "node-operand-images.json").exists()
        return
    assert result.returncode == 73, result.stdout + result.stderr
    assert {c[-1] for c in calls if c[3] == "pull"} == set(images.values())
    assert sum(c[3] == "inspecti" and c[-1] == "sha256:config" for c in calls) == 2
