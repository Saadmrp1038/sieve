"""Cluster setup loads published images through Docker and kind's command seam."""

import functools
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import kubernetes
import pytest

from sieve_tool.paths import repository
from sieve_tool.process import ProcessError, execution
from sieve_tool.runtime.cluster import setup_cluster, teardown_cluster


@pytest.fixture
def image_commands(tmp_path, monkeypatch):
    class API(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            payload = json.dumps(
                {
                    "items": [
                        {"metadata": {"name": "api"}, "status": {"phase": "Running"}}
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "test",
                "clusters": [
                    {
                        "name": "test",
                        "cluster": {"server": f"http://127.0.0.1:{server.server_port}"},
                    }
                ],
                "contexts": [
                    {"name": "test", "context": {"cluster": "test", "user": "test"}}
                ],
                "users": [{"name": "test", "user": {}}],
            }
        )
    )
    monkeypatch.setattr(
        kubernetes.config,
        "load_kube_config",
        functools.partial(
            kubernetes.config.load_kube_config, config_file=str(kubeconfig)
        ),
    )
    # A Docker 29 image store can retain an index without all its platform blobs.
    # The command adapters only accept an archive containing the node's platform.
    commands = {
        "kind": """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[:2]==["load","docker-image"]:
    if os.environ.get("IMAGE_MODE")=="native" or (os.environ.get("IMAGE_MODE")=="fresh" and Path(os.environ["IMAGE_AVAILABLE"]).exists()):
        Path(os.environ["IMAGE_LOADED"]).write_text("native")
        sys.exit(0)
    sys.exit("ctr: content digest sha256:other-platform: not found")
elif args[:2]==["load","image-archive"]:
    assert json.loads(Path(args[2]).read_text())=={"platform":os.environ["NODE_PLATFORM"],"image":"local/operator:vanilla"}
    if os.environ.get("ARCHIVE_FAILURE")=="import":sys.exit("archive import failed")
    Path(os.environ["IMAGE_LOADED"]).write_text(args[2])
elif args[:2] not in (["get","clusters"],["create","cluster"],["delete","cluster"]):
    sys.exit("unexpected kind command")
""",
        "docker": """import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[0]=="pull":
    if os.environ.get("IMAGE_MODE")!="fresh":sys.exit("pull access denied for local/operator")
    Path(os.environ["IMAGE_AVAILABLE"]).touch()
elif args[0]=="inspect":print("sha256:node")
elif args[:2]==["image","inspect"]:
    if os.environ.get("IMAGE_MODE")=="fresh" and "sha256:node" not in args and not Path(os.environ["IMAGE_AVAILABLE"]).exists():sys.exit("no such image")
    print(os.environ["NODE_PLATFORM"] if "sha256:node" in args else "[]")
elif args[:2]==["image","save"]:
    platform=args[args.index("--platform")+1]
    archive=Path(args[args.index("--output")+1])
    Path(os.environ["ARCHIVE_PATH"]).write_text(str(archive))
    archive.write_text(json.dumps({"platform":platform,"image":args[-1]}))
    if os.environ.get("ARCHIVE_FAILURE")=="export":sys.exit("archive export failed")
elif args[0]!="exec":sys.exit("unexpected Docker command")
""",
        "kubectl": "import sys\nassert sys.argv[1]=='apply'\n",
    }
    for name, source in commands.items():
        command = tmp_path / name
        command.write_text(f"#!{sys.executable}\n" + source)
        command.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    loaded = tmp_path / "image-loaded"
    monkeypatch.setenv("IMAGE_LOADED", str(loaded))
    monkeypatch.setenv("IMAGE_AVAILABLE", str(tmp_path / "image-available"))
    monkeypatch.setenv("NODE_PLATFORM", "linux/amd64")
    monkeypatch.setenv("ARCHIVE_PATH", str(tmp_path / "archive-path"))
    plan = tmp_path / "vanilla.yaml"
    plan.write_text("actions: []\n")
    context = SimpleNamespace(
        action_types=[],
        num_apiservers=1,
        num_workers=0,
        mode="vanilla",
        controller="operator",
        container_registry="local",
        image_tag="vanilla",
        controller_config=SimpleNamespace(kubernetes_version="v1.36.4"),
        result_dir=str(tmp_path),
        test_plan=str(plan),
    )
    try:
        with repository(tmp_path), execution(log_dir=tmp_path / "commands"):
            try:
                yield context, tmp_path
            finally:
                teardown_cluster()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("platform", ["linux/amd64", "linux/arm64"])
def test_cluster_loads_the_node_platform_when_other_image_manifests_are_absent(
    image_commands, monkeypatch, platform
):
    context, directory = image_commands
    monkeypatch.setenv("NODE_PLATFORM", platform)
    setup_cluster(context)
    assert (directory / "image-loaded").exists(), "cluster has no imported image"
    assert not Path((directory / "archive-path").read_text()).parent.exists()


@pytest.mark.parametrize("failure", ["export", "import"])
def test_bad_image_archives_fail_setup_and_remove_temporary_files(
    image_commands, monkeypatch, failure
):
    context, directory = image_commands
    monkeypatch.setenv("ARCHIVE_FAILURE", failure)
    with pytest.raises(
        ProcessError, match=f"{failure}.*controller image|load controller image archive"
    ):
        setup_cluster(context)
    assert not (directory / "image-loaded").exists()
    assert not Path((directory / "archive-path").read_text()).parent.exists()


@pytest.mark.parametrize("mode", ["native", "fresh"])
def test_loadable_images_do_not_need_archive_export(image_commands, monkeypatch, mode):
    context, directory = image_commands
    monkeypatch.setenv("IMAGE_MODE", mode)
    setup_cluster(context)
    assert (directory / "image-loaded").read_text() == "native"
    assert not (directory / "archive-path").exists()
