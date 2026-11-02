"""Public image-build behavior with external Git, compiler and daemon fixtures."""

import json
import os
import subprocess
import sys


def test_build_uses_instrumenter_requirement_and_preserves_native_compiler(tmp_path):
    checkout = tmp_path / "sieve"
    profile = checkout / "controllers/operator"
    build = profile / "build"
    build.mkdir(parents=True)
    (checkout / "go.work").touch()
    client = checkout / "go/client"
    client.mkdir(parents=True)
    (client / "go.mod").write_text("module sieve.client\ngo 1.26.0\n")
    instrumenter = checkout / "go/instrumenter"
    instrumenter.mkdir()
    (instrumenter / "go.mod").write_text("module sieve.instrumenter\ngo 1.27.0\n")
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    (upstream / "go.mod").write_text("module operator\ngo 1.26.0\n")
    git = ["git", "-C", str(upstream)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@localhost",
            "commit",
            "-qm",
            "native operator",
        ],
        check=True,
    )
    revision = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    (profile / "config.json").write_text(
        json.dumps(
            {
                "name": "operator",
                "github_link": str(upstream),
                "commit": revision,
                "kubernetes_version": "v1.36.4",
                "client_go_version": "v0.36.2",
                "go_mod": "vendor",
                "vendored_client_go_path": "vendor/k8s.io/client-go",
                "vendored_sieve_client_path": "vendor/sieve.client",
                "dockerfile_path": "Dockerfile",
                "controller_image_name": "fixture:latest",
                "test_command": ["unused"],
                "custom_resource_definitions": [],
                "annotated_reconcile_functions": {},
                "controller_pod_label": "operator",
                "controller_deployment_file_path": "unused.yaml",
            }
        )
    )
    (build / "Dockerfile").write_text("FROM scratch\nCOPY controller /controller\n")
    (build / "build.sh").write_text(
        "set -eu\n"
        "test -f instrumented.txt\n"
        "go build -o controller .\n"
        "docker build -t fixture:latest .\n"
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    # The compiler fixture enforces each module's Go minimum. Its generated
    # instrumenter consumes the public JSON configuration and marks its output.
    instrumenter_program = (
        f"#!{sys.executable}\n"
        "import json,sys\nfrom pathlib import Path\n"
        "c=json.loads(Path(sys.argv[1]).read_text())\n"
        "(Path(c['app_file_path'])/'instrumented.txt').write_text('test instrumentation')\n"
    )
    compiler = tools / "go"
    compiler.write_text(
        f"#!{sys.executable}\n"
        "import json, os, re, sys\n"
        "from pathlib import Path\n"
        "assert sys.argv[1] == 'build'\n"
        "module = Path('go.mod').read_text()\n"
        "required = re.search(r'^go (\\S+)', module, re.M)[1]\n"
        "selected = os.environ.get('GOTOOLCHAIN', 'auto')\n"
        "version = selected[2:] if selected.startswith('go') else '1.27.3'\n"
        "if tuple(map(int, version.split('.'))) < tuple(map(int, required.split('.'))):\n"
        " print('module requires Go >= ' + required + '; selected ' + version, file=sys.stderr)\n"
        " sys.exit(1)\n"
        "output = Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "if module.startswith('module sieve.instrumenter'):\n"
        f" output.write_text({instrumenter_program!r})\n"
        " output.chmod(0o700)\n"
        "else:\n"
        " assert Path('instrumented.txt').read_text() == 'test instrumentation'\n"
        " output.write_text(json.dumps({'native_compiler': version, 'instrumented': True}))\n"
    )
    compiler.chmod(0o700)
    daemon = tools / "docker"
    daemon.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == 'build':\n"
        " Path(os.environ['FIXTURE_IMAGE']).write_text(Path('controller').read_text())\n"
        "elif sys.argv[1] == 'tag':\n"
        " p=Path(os.environ['FIXTURE_IMAGE']); image=json.loads(p.read_text())\n"
        " image['tag']=sys.argv[-1]; p.write_text(json.dumps(image))\n"
        "else:\n"
        " assert sys.argv[1] == 'ps', sys.argv\n"
    )
    daemon.chmod(0o700)
    image = tmp_path / "image.json"
    environment = dict(
        os.environ,
        PATH=str(tools) + os.pathsep + os.environ["PATH"],
        GOTOOLCHAIN="go1.26.10",
        FIXTURE_IMAGE=str(image),
        SIEVE_REAL_DOCKER=str(daemon),
    )
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
            "test",
            "--registry",
            "verified-fixture",
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(image.read_text()) == {
        "native_compiler": "1.26.10",
        "instrumented": True,
        "tag": "verified-fixture/operator:test",
    }
