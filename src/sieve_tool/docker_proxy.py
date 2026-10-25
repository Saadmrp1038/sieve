"""Label containers created by a build, then exec the real Docker client."""

import os
import sys


def main():
    arguments = sys.argv[1:]
    if (
        arguments
        and arguments[0].startswith("-")
        and any(value in arguments for value in ("run", "create"))
    ):
        raise SystemExit(
            "Configure the build Docker endpoint through its environment/context; global Docker options cannot be tracked safely"
        )
    if arguments and arguments[0] in ("run", "create"):
        arguments[1:1] = ["--label", "sieve.build=" + os.environ["SIEVE_DOCKER_OWNER"]]
    real = os.environ["SIEVE_REAL_DOCKER"]
    os.execv(real, [real, *arguments])


if __name__ == "__main__":
    main()
