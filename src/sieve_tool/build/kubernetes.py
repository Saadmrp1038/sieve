"""Download, instrument, and build Kubernetes node images."""

import os
import shutil
import sys
from pathlib import Path

from sieve_tool.common.common import rmtree_if_exists
from sieve_tool.paths import cache_path, repository_root
from sieve_tool.process import ProcessError, ProcessTimeout, bounded, run

from .instrumenter import run_instrumenter
from .resources import docker_build_scope


def kubernetes_source():
    return cache_path("kubernetes", "src", "k8s.io", "kubernetes")


@bounded("network")
def download_kubernetes(version, *, checkout=None):
    """
    Download Kubernetes into the build cache and check out the specified version.

    :param version: the Kubernetes version that will be used to build kind node image
    """
    checkout = Path(checkout) if checkout is not None else kubernetes_source()
    os.makedirs(os.path.dirname(checkout), exist_ok=True)
    cached = (
        run(
            ["git", "-C", checkout, "rev-parse", "--verify", version + "^{commit}"],
            capture_output=True,
            check=False,
            operation="download kubernetes",
            category="network",
        )
        if os.path.isdir(os.path.join(checkout, ".git"))
        else None
    )
    if cached is not None and cached.returncode == 0:
        revision = cached.stdout.strip()
        print("Reusing Kubernetes checkout at {} ({})".format(version, revision))
        run(
            ["git", "-C", checkout, "reset", "--hard", revision],
            check=True,
            operation="download kubernetes",
            category="network",
        )
        run(
            ["git", "-C", checkout, "clean", "-fd"],
            check=True,
            operation="download kubernetes",
            category="network",
        )
        for relative in ("vendor/sieve.client", "staging/src/sieve.client"):
            dependency = os.path.join(checkout, relative)
            if os.path.islink(dependency):
                os.unlink(dependency)
            else:
                rmtree_if_exists(dependency)
    else:
        for attempt in range(3):
            rmtree_if_exists(checkout)
            try:
                run(
                    [
                        "git",
                        "-c",
                        "http.version=HTTP/1.1",
                        "clone",
                        "--depth",
                        "1",
                        "--single-branch",
                        "--branch",
                        version,
                        "https://github.com/kubernetes/kubernetes.git",
                        checkout,
                    ],
                    check=True,
                    timeout=300,
                    operation="download kubernetes",
                    category="network",
                )
                break
            except ProcessError, ProcessTimeout:
                if attempt == 2:
                    raise
                print(
                    "Kubernetes source download failed; retrying ({}/3)".format(
                        attempt + 2
                    )
                )
    run(
        ["git", "-C", checkout, "checkout", "-B", "sieve", version],
        check=True,
        operation="download kubernetes",
        category="network",
    )


def install_client(version):
    checkout = kubernetes_source()
    with (checkout / "staging/src/k8s.io/apiserver/go.mod").open("a") as stream:
        stream.write(
            "require sieve.client v0.0.0\nreplace sieve.client => ../../sieve.client\n"
        )
    destination = checkout / "staging/src/sieve.client"
    shutil.copytree(repository_root() / "go/client", destination)
    (checkout / "vendor/sieve.client").symlink_to("../staging/src/sieve.client")


@bounded("build")
def build_node(version, mode, registry, push):
    download_kubernetes(version)
    install_client(version)
    checkout = kubernetes_source()
    run_instrumenter(
        {"project": "kubernetes", "mode": mode, "k8s_filepath": str(checkout)}
    )
    common = checkout / "build/common.sh"
    script = common.read_text()
    common.write_text(
        script.replace(
            "{{ .NetworkSettings.IPAddress }}",
            '{{with index .NetworkSettings.Networks "bridge"}}{{.IPAddress}}{{end}}',
        )
    )
    revision = run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        capture_output=True,
        operation="build node",
        category="build",
    ).stdout.strip()
    with docker_build_scope() as environment:
        run(
            ["kind", "build", "node-image"],
            cwd=checkout,
            env={
                **environment,
                "GOPATH": str(cache_path("kubernetes")),
                "GOWORK": "off",
                "KUBE_GIT_VERSION": f"{version}-sieve-{revision}",
            },
            check=True,
            operation="build node",
            category="build",
        )
    platform = "macos-" if sys.platform == "darwin" else ""
    image = f"{registry}/node:{version}-{platform}{mode}"
    run(
        ["docker", "image", "tag", "kindest/node:latest", image],
        check=True,
        operation="build node",
        category="build",
    )
    if push:
        run(
            ["docker", "push", image],
            check=True,
            operation="build node",
            category="build",
        )


def build_nodes(version, mode, registry, push=False):
    modes = ("learn", "test", "vanilla") if mode == "all" else (mode,)
    for selected in modes:
        build_node(version, selected, registry, push)
