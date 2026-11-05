"""Native source must still compile after the public image-build command."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def test_instrumented_build_preserves_a_dependency_whose_package_name_differs(tmp_path):
    compiler = shutil.which("go")
    if compiler is None:
        pytest.skip("public build contract requires an external Go compiler")
    source_root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "sieve"
    for module in ("client", "instrumenter"):
        shutil.copytree(source_root / "go" / module, checkout / "go" / module)
    (checkout / "go.work").touch()
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "go.mod").write_text(
        "module example\ngo 1.27.0\n"
        "require github.com/opencontainers/go-digest v1.0.0\n"
        "replace github.com/opencontainers/go-digest => ./digest\n"
        "replace sieve.client => ./vendor/sieve.client\n"
    )
    digest = upstream / "digest"
    digest.mkdir()
    (digest / "go.mod").write_text(
        "module github.com/opencontainers/go-digest\ngo 1.27.0\n"
    )
    (digest / "digest.go").write_text("package digest\ntype Digest string\n")
    (upstream / "main.go").write_text(
        'package main\nimport ("fmt"; "github.com/opencontainers/go-digest")\n'
        "type Reconciler struct{}\n"
        'func (r *Reconciler) Reconcile() digest.Digest {return digest.Digest("sha256:synthetic")}\n'
        "func main(){fmt.Print((&Reconciler{}).Reconcile())}\n"
    )
    client = upstream / "vendor/k8s.io/client-go"
    for fixture, name in (
        ("request", "rest/request.go"),
        ("store", "tools/cache/store.go"),
        ("informer", "tools/cache/shared_informer.go"),
    ):
        target = client / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(
            source_root / "go/instrumenter/testdata" / fixture / "input.go.txt", target
        )
    git = ["git", "-C", str(upstream)]
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@localhost",
            "commit",
            "-qm",
            "native source",
        ],
    ):
        subprocess.run([*git, *args], check=True)
    pin = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    profile = checkout / "controllers/operator"
    build = profile / "build"
    build.mkdir(parents=True)
    (profile / "config.json").write_text(
        json.dumps(
            {
                "name": "operator",
                "github_link": str(upstream),
                "commit": pin,
                "kubernetes_version": "v1.36.4",
                "client_go_version": "v0.37.1",
                "go_mod": "vendor",
                "vendored_client_go_path": "vendor/k8s.io/client-go",
                "vendored_sieve_client_path": "vendor/sieve.client",
                "dockerfile_path": "Dockerfile",
                "controller_image_name": "fixture:latest",
                "test_command": ["unused"],
                "custom_resource_definitions": [],
                "annotated_reconcile_functions": {
                    "main.go": "example.(*Reconciler).Reconcile"
                },
                "controller_pod_label": "operator",
                "controller_deployment_file_path": "unused.yaml",
            }
        )
    )
    (build / "Dockerfile").write_text("FROM scratch\nCOPY controller /controller\n")
    (build / "build.sh").write_text(
        "set -eu\nexport GOWORK=off\ngo build -mod=mod -o controller .\n"
        'test "$(./controller)" = "sha256:synthetic"\n'
        "docker build -t fixture:latest .\n"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    daemon = tools / "docker"
    receipt = tmp_path / "image.txt"
    daemon.write_text(
        f"#!{sys.executable}\nimport os,sys\nfrom pathlib import Path\n"
        "if sys.argv[1]=='tag': Path(os.environ['FIXTURE_IMAGE']).write_text(sys.argv[-1])\n"
        "else: assert sys.argv[1] in ('ps','build'),sys.argv\n"
    )
    daemon.chmod(0o700)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sieve_tool",
            "--repo",
            str(checkout),
            "build",
            "-c",
            "operator",
            "-m",
            "learn",
            "--registry",
            "verified-fixture",
        ],
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "SIEVE_REAL_DOCKER": str(daemon),
            "FIXTURE_IMAGE": str(receipt),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert receipt.read_text() == "verified-fixture/operator:learn"
