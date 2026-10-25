"""Track daemon-side build containers separately from their client processes."""

import json
import os
import shlex
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sieve_tool.process import cleanup_budget, reset_cleanup_budget, run, settings


@contextmanager
def docker_build_scope():
    docker = os.environ.get("SIEVE_REAL_DOCKER") or shutil.which("docker")
    if docker is None:
        raise FileNotFoundError("docker is not installed")
    owner = uuid4().hex
    root = settings()[1] / ("docker-" + owner)
    root.mkdir(parents=True)
    shutil.copy(Path(__file__).parents[1] / "docker_proxy.py", root / "proxy.py")
    wrapper = root / "docker"
    wrapper.write_text(
        "#!/bin/sh\nexec "
        + shlex.quote(sys.executable)
        + " "
        + shlex.quote(str(root / "proxy.py"))
        + ' "$@"\n'
    )
    wrapper.chmod(0o700)
    record = root / "resources.json"
    record.write_text(
        json.dumps(
            {"owner_label": "sieve.build=" + owner, "status": "building"}, indent=2
        )
        + "\n"
    )
    reset_cleanup_budget()
    failure = None
    try:
        # BuildKit cancels server-side builds on client disconnection. The
        # separate container lease also covers docker run/create build helpers.
        yield {
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "SIEVE_REAL_DOCKER": docker,
            "SIEVE_DOCKER_OWNER": owner,
            "DOCKER_BUILDKIT": "1",
        }
    except BaseException as error:
        failure = error
        raise
    finally:
        try:
            with cleanup_budget():
                args = [docker, "ps", "-q", "--filter", "label=sieve.build=" + owner]
                if failure is not None:
                    args.insert(2, "-a")
                containers = run(
                    args,
                    operation="find owned build containers",
                    category="request",
                    capture_output=True,
                ).stdout.split()
                if containers:
                    run(
                        [docker, "rm", "-f", *containers],
                        operation="remove owned build containers",
                        category="cleanup",
                    )
                remaining = run(
                    [docker, "ps", "-q", "--filter", "label=sieve.build=" + owner],
                    operation="verify build containers stopped",
                    category="request",
                    capture_output=True,
                ).stdout.split()
                if remaining:
                    raise RuntimeError("Owned build containers are still running")
            record.write_text(
                json.dumps(
                    {
                        "owner_label": "sieve.build=" + owner,
                        "status": "cleaned",
                        "removed_containers": containers,
                    },
                    indent=2,
                )
                + "\n"
            )
        except Exception as error:
            try:
                record.write_text(
                    json.dumps(
                        {
                            "owner_label": "sieve.build=" + owner,
                            "status": "cleanup_failed",
                            "error": str(error),
                        },
                        indent=2,
                    )
                    + "\n"
                )
            except OSError:
                pass
            if failure is None:
                raise
            failure.add_note(
                f"Docker cleanup also failed: {error}; ownership record: {record}"
            )
