import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from sieve_tool.common.common import add_mod_recursively

DEFAULT_K8S_VERSION = "v1.18.9"


def k8s_version_to_apimachinery_version(k8s_version):
    """
    Generate the version number of apimachinery imported by sieve client.

    :param k8s_version: the Kubernetes version
    :return: the apimachinery version
    """
    # TODO: It is a bit ad-hoc to decide the compatible apimachinery version
    apimachinery_version = "v0" + k8s_version[2:]
    print(
        "Use the apimachinery version {} in sieve client".format(apimachinery_version)
    )
    return apimachinery_version


def update_sieve_client_go_mod_with_version(go_mod_path, version):
    """
    Update the apimachinery version number in the go.mod of sieve client.
    We need to hack the go.mod because sieve client will be imported by Kubernetes after instrumentation.

    :param go_mod_path: the file path of go.mod in sieve client
    :param version: the apimachinery version number
    """
    fin = open(go_mod_path)
    data = fin.read()
    data = data.replace(
        "k8s.io/apimachinery v0.18.9",
        "k8s.io/apimachinery {}".format(version),
    )
    fin.close()
    fout = open(go_mod_path, "w")
    fout.write(data)
    fout.close()


def install_module(module, sieve_dep_src_dir):
    """Use Go's resolved module directory, including custom module caches."""
    result = subprocess.run(
        ["go", "mod", "download", "-json", module],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GOWORK": "off"},
    )
    metadata = json.loads(result.stdout)
    destination = Path(sieve_dep_src_dir) / module
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(metadata["Dir"], destination)
    add_mod_recursively(destination, stat.S_IWRITE)
