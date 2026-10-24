"""Download, instrument, and build Kubernetes node images."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

from sieve_tool.common.common import rmtree_if_exists
from sieve_tool.paths import cache_path, repository_root
from .dependencies import (
    DEFAULT_K8S_VERSION,
    k8s_version_to_apimachinery_version,
    update_sieve_client_go_mod_with_version,
)
from .instrumenter import run_instrumenter


def kubernetes_source():
    return cache_path("kubernetes", "src", "k8s.io", "kubernetes")


def download_kubernetes(version, *, checkout=None):
    """
    Download Kubernetes into the build cache and check out the specified version.

    :param version: the Kubernetes version that will be used to build kind node image
    """
    checkout = Path(checkout) if checkout is not None else kubernetes_source()
    os.makedirs(os.path.dirname(checkout), exist_ok=True)
    cached = (
        subprocess.run(
            ["git", "-C", checkout, "rev-parse", "--verify", version + "^{commit}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if os.path.isdir(os.path.join(checkout, ".git"))
        else None
    )
    if cached is not None and cached.returncode == 0:
        revision = cached.stdout.strip()
        print("Reusing Kubernetes checkout at {} ({})".format(version, revision))
        subprocess.run(["git", "-C", checkout, "reset", "--hard", revision], check=True)
        # Retain ignored build output while removing prior instrumentation.
        subprocess.run(["git", "-C", checkout, "clean", "-fd"], check=True)
        for relative in ("vendor/sieve.client", "staging/src/sieve.client"):
            dependency = os.path.join(checkout, relative)
            if os.path.islink(dependency):
                os.unlink(dependency)
            else:
                rmtree_if_exists(dependency)
    else:
        # The release source is sufficient; fetching all Kubernetes history is
        # slow and makes each node-image build vulnerable to long TLS failures.
        for attempt in range(3):
            rmtree_if_exists(checkout)
            try:
                subprocess.run(
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
                    stdout=subprocess.DEVNULL,
                )
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                print(
                    "Kubernetes source download failed; retrying ({}/3)".format(
                        attempt + 2
                    )
                )
    subprocess.run(
        ["git", "-C", checkout, "checkout", "-B", "sieve", version], check=True
    )


def install_client(version):
    checkout = kubernetes_source()
    with (checkout / "staging/src/k8s.io/apiserver/go.mod").open("a") as stream:
        stream.write(
            "require sieve.client v0.0.0\nreplace sieve.client => ../../sieve.client\n"
        )
    destination = checkout / "staging/src/sieve.client"
    shutil.copytree(repository_root() / "go/client", destination)
    if version != DEFAULT_K8S_VERSION:
        update_sieve_client_go_mod_with_version(
            destination / "go.mod", k8s_version_to_apimachinery_version(version)
        )
    (checkout / "vendor/sieve.client").symlink_to("../staging/src/sieve.client")


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
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=checkout, text=True
    ).strip()
    subprocess.run(
        ["kind", "build", "node-image"],
        cwd=checkout,
        env={
            **os.environ,
            "GOPATH": str(cache_path("kubernetes")),
            "KUBE_GIT_VERSION": f"{version}-sieve-{revision}",
        },
        check=True,
    )
    platform = "macos-" if sys.platform == "darwin" else ""
    image = f"{registry}/node:{version}-{platform}{mode}"
    subprocess.run(["docker", "image", "tag", "kindest/node:latest", image], check=True)
    if push:
        subprocess.run(["docker", "push", image], check=True)


def build_nodes(version, mode, registry, push=False):
    modes = ("learn", "test", "vanilla") if mode == "all" else (mode,)
    for selected in modes:
        build_node(version, selected, registry, push)
