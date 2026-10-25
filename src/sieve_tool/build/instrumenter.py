"""Build and invoke the Go instrumenter without generated files in source."""

import json
import os
import tempfile
from pathlib import Path

from sieve_tool.paths import cache_path, repository_root
from sieve_tool.process import run


def run_instrumenter(config):
    binary = cache_path("bin", "sieve-instrumenter")
    binary.parent.mkdir(parents=True, exist_ok=True)
    run(
        ["go", "build", "-o", str(binary), "."],
        cwd=repository_root() / "go/instrumenter",
        env={**os.environ, "GOWORK": "off"},
        check=True,
        operation="run instrumenter",
        category="compile",
    )
    directory = cache_path("instrumenter")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        path = Path(temporary) / "config.json"
        path.write_text(json.dumps(config, indent=2))
        run(
            [str(binary), str(path)],
            check=True,
            operation="run instrumenter",
            category="compile",
        )
