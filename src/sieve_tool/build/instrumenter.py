"""Build and invoke the Go instrumenter without generated files in source."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from sieve_tool.paths import cache_path, repository_root


def run_instrumenter(config):
    binary = cache_path("bin", "sieve-instrumenter")
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["go", "build", "-o", str(binary), "."],
        cwd=repository_root() / "go/instrumenter",
        env={**os.environ, "GOWORK": "off"},
        check=True,
    )
    directory = cache_path("instrumenter")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        path = Path(temporary) / "config.json"
        path.write_text(json.dumps(config, indent=2))
        subprocess.run([str(binary), str(path)], check=True)
