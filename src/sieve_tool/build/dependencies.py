import json
import os
import shutil
import stat
from pathlib import Path

from sieve_tool.common.common import add_mod_recursively
from sieve_tool.process import run


def install_module(module, sieve_dep_src_dir):
    """Use Go's resolved module directory, including custom module caches."""
    result = run(
        ["go", "mod", "download", "-json", module],
        check=True,
        env={**os.environ, "GOWORK": "off"},
        capture_output=True,
        operation="install module",
        category="network",
    )
    metadata = json.loads(result.stdout)
    destination = Path(sieve_dep_src_dir) / module
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(metadata["Dir"], destination)
    add_mod_recursively(destination, stat.S_IWRITE)
