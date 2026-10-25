"""Check the tools required to build images and execute workloads."""

from importlib import import_module
import shutil
from sieve_tool.process import run as run_process, ProcessError

from sieve_tool.common.common import fail, ok, warn


def run():
    missing = []
    for name, arguments in {
        "go": ["version"],
        "docker": ["info", "--format", "{{.ServerVersion}}"],
        "kind": ["version"],
        "kubectl": ["version", "--client=true"],
    }.items():
        if not shutil.which(name):
            fail(f"{name} is missing from PATH")
            missing.append(name)
            continue
        try:
            result = run_process(
                [name, *arguments],
                capture_output=True,
                operation=f"check {name}",
                timeout=30,
                check=True,
            )
            ok(f"{name}: {result.stdout.strip()}")
        except (ProcessError, OSError) as error:
            fail(f"{name} check failed: {error}")
            missing.append(name)
    for module in ("kubernetes", "docker", "yaml", "deepdiff"):
        try:
            import_module(module)
        except ImportError:
            fail(f"Python module {module} is missing; run uv sync --locked")
            missing.append(module)
    for name in ("helm", "mage"):
        if not shutil.which(name):
            warn(f"{name} is optional; install it if your controller build needs it")
    return 1 if missing else 0
