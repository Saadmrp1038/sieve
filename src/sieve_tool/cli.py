"""The installed Sieve command-line interface."""

import argparse
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from sieve_tool.paths import find_repository, repository
from sieve_tool.process import execution, cancellation_signals, Cancelled, ProcessError


def parser():
    command = argparse.ArgumentParser(
        prog="sieve", description="Reliability testing for Kubernetes controllers"
    )
    command.add_argument(
        "--repo", type=Path, help="Sieve checkout containing controllers and Go sources"
    )
    command.add_argument(
        "--config", type=Path, help="JSON overrides for the packaged harness defaults"
    )
    commands = command.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build node or controller images")
    build.add_argument(
        "-c", "--controller", help="Controller name; omit to build Kubernetes nodes"
    )
    build.add_argument(
        "-m", "--mode", choices=("learn", "test", "vanilla", "all"), required=True
    )
    build.add_argument("--kubernetes-version", default="v1.18.9")
    build.add_argument(
        "--revision", help="Override the controller's pinned Git revision"
    )
    build.add_argument("--build-only", action="store_true")
    build.add_argument("--push", action="store_true")
    build.add_argument("-r", "--registry")
    for mode in ("learn", "test", "vanilla"):
        run = commands.add_parser(mode, help=f"Run a {mode} workload")
        run.add_argument("-c", "--controller", required=True)
        run.add_argument("-w", "--workload", required=mode != "test")
        run.add_argument(
            "-o",
            "--output",
            type=Path,
            help=f"Result root; default runs/{mode}/<unique-run-id>",
        )
        run.add_argument("-r", "--registry")
        run.add_argument(
            "--oracle-root",
            type=Path,
            help="Reference root containing controller/workload directories; default runs/oracles",
        )
        run.add_argument("--postprocess", action="store_true")
        if mode == "learn":
            run.add_argument(
                "--build-oracle", action=argparse.BooleanOptionalAction, default=True
            )
        elif mode == "test":
            run.add_argument("-p", "--plan", type=Path, required=True)
            run.add_argument(
                "--batch",
                action="store_true",
                help="Execute all YAML plans in the given directory",
            )
    reproduce = commands.add_parser(
        "reproduce", help="Run historical bug reproductions"
    )
    reproduce.add_argument("-c", "--controller", required=True)
    reproduce.add_argument("-b", "--bug", default="all")
    reproduce.add_argument("-r", "--registry")
    reproduce.add_argument(
        "-o", "--output", type=Path, help="Result root; required with --skip"
    )
    reproduce.add_argument("--skip", action="store_true", help="Read existing results")
    report = commands.add_parser(
        "report", help="Report potential bugs from saved results"
    )
    report.add_argument("--results", type=Path)
    commands.add_parser("doctor", help="Check external tools and Python dependencies")
    commands.add_parser(
        "check-plans", help="Compare learned plans with historical reproductions"
    )
    controller = commands.add_parser(
        "controller", help="Manage controller integrations"
    )
    operations = controller.add_subparsers(dest="operation", required=True)
    initialize = operations.add_parser("init", help="Create a controller integration")
    initialize.add_argument("name")
    ci = commands.add_parser("ci", help="Manage generated workflows")
    ci.add_argument("operation", choices=("generate",))
    return command


def _controller(root, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Controller must be a name under controllers/, not a path")
    directory = root / "controllers" / name
    if not (directory / "config.json").is_file():
        raise ValueError(f"Controller {name!r} not found under {root / 'controllers'}")
    return str(directory)


def _input(path, root):
    if path is None:
        return None
    path = path.expanduser()
    if not path.is_absolute() and not path.exists():
        path = root / path
    return path.resolve()


def successful(result, context):
    if result is None:
        path = Path(context.result_dir) / "workload.log"
        return path.is_file() and path.read_text().strip() == "FINISH-SIEVE-TEST"
    return (
        result.no_exception
        and result.injection_completed
        and result.workload_completed
        and not (
            result.common_errors or result.end_state_errors or result.history_errors
        )
    )


def main(argv=None):
    try:
        with cancellation_signals():
            return _main(argv)
    except Cancelled as error:
        print(f"sieve: {error}", file=sys.stderr)
        return 128 + error.signum
    except KeyboardInterrupt:
        print("sieve: interrupted", file=sys.stderr)
        return 130


def _main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "doctor":
            from sieve_tool.doctor import run

            return run()
        root = find_repository(arguments.repo)
        # Resolve user paths before entering the repository's execution scope.
        for name in ("config", "plan", "oracle_root", "results"):
            if hasattr(arguments, name):
                setattr(arguments, name, _input(getattr(arguments, name), root))
        if getattr(arguments, "output", None) is not None:
            arguments.output = arguments.output.expanduser().resolve()
        with repository(root):
            from sieve_tool.common.config import get_common_config

            common = get_common_config(arguments.config)
            with execution(
                common.process_timeouts, root / ".cache/commands" / uuid4().hex
            ):
                return _dispatch(arguments, root, common)
    except (ValueError, OSError) as error:
        print(f"sieve: {error}", file=sys.stderr)
        return 1
    except ProcessError as error:
        print(f"sieve: {error}", file=sys.stderr)
        return 1


def _dispatch(arguments, root, common):
    from sieve_tool.common.config import get_common_config, load_controller_config

    registry = getattr(arguments, "registry", None) or common.container_registry
    if arguments.command == "build":
        if arguments.controller:
            from sieve_tool.build.controllers import build_controllers

            directory = _controller(root, arguments.controller)
            config = load_controller_config(directory)
            if arguments.revision:
                config.commit = arguments.revision
            if arguments.mode == "all" and arguments.build_only:
                raise ValueError("--build-only requires a single build mode")
            build_controllers(
                directory,
                config,
                arguments.mode,
                arguments.build_only,
                arguments.push,
                registry,
            )
        else:
            from sieve_tool.build.kubernetes import build_nodes

            build_nodes(
                arguments.kubernetes_version, arguments.mode, registry, arguments.push
            )
        return 0
    if arguments.command in ("learn", "test", "vanilla"):
        from sieve_tool.runtime import runner

        mode = arguments.command
        directory = _controller(root, arguments.controller)
        identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
        output = str(arguments.output or root / "runs" / mode / identifier)
        build_oracle = getattr(arguments, "build_oracle", False)
        if arguments.postprocess and build_oracle:
            raise ValueError("--postprocess requires --no-build-oracle")
        plan = str(arguments.plan) if mode == "test" else f"{mode}.yaml"
        options = dict(
            oracle_root=str(arguments.oracle_root or root / "runs/oracles"),
            common_config=common,
        )
        if getattr(arguments, "batch", False):
            results = runner.run_batch(
                directory,
                arguments.workload,
                output,
                mode,
                plan,
                registry,
                arguments.postprocess,
                build_oracle,
                **options,
            )
            return (
                0
                if results
                and all(successful(result, context) for result, context in results)
                else 1
            )
        started = time.time()
        result, context = runner.run(
            directory,
            arguments.workload,
            output,
            mode,
            plan,
            registry,
            arguments.postprocess,
            build_oracle,
            **options,
        )
        runner.save_run_result(context, result, started)
        return 0 if successful(result, context) else 1
    if arguments.command == "reproduce":
        from sieve_tool.reproduce import run

        if arguments.skip and arguments.output is None:
            raise ValueError(
                "--skip requires --output pointing to a previous reproduction run"
            )
        identifier = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid4().hex[:8]
        )
        output = arguments.output or root / "runs/test" / identifier
        return run(
            arguments.controller,
            arguments.bug,
            registry,
            arguments.skip,
            output,
            common,
        )
    if arguments.command == "report":
        from sieve_tool.report import report

        return report(arguments.results or root / "runs/test")
    if arguments.command == "controller":
        from sieve_tool.scaffold import create

        create(root / "controllers", arguments.name)
        return 0
    if arguments.command == "ci":
        from sieve_tool.ci import generate_workflows

        generate_workflows(root / ".github/workflows")
        return 0
    if arguments.command == "check-plans":
        from sieve_tool.plan_check import check

        return check(root)
    raise ValueError(f"Unsupported command: {arguments.command}")
