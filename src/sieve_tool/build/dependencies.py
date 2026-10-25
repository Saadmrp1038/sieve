import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from sieve_tool.common.common import add_mod_recursively


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
