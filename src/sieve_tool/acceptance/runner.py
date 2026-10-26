"""Run the maintained acceptance suite through the ordinary Sieve CLI."""

import json
import shutil
import sys
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from sieve_tool.acceptance.evidence import (
    Phase,
    RunRecord,
    verify,
    verify_case,
    verify_workload,
)
from sieve_tool.acceptance.suite import (
    IMAGE_ADAPTER,
    MODES,
    ImageManifest,
    Selection,
    Suite,
    build_inputs,
    image_references,
    load_suite,
    save_json,
    suite_directory,
)
from sieve_tool.common.config import (
    CommonConfig,
    ControllerConfig,
    load_controller_config,
)
from sieve_tool.oracle.artifacts import read_artifact
from sieve_tool.process import (
    Cancelled,
    ProcessError,
    cleanup_budget,
    execution,
    reset_cleanup_budget,
    run,
    validate_timeouts,
)
from sieve_tool.revisions import SourceRevision, verify_revision


def _source(root: Path, expected: str) -> SourceRevision:
    if sys.platform != "linux":
        raise ValueError(
            "Acceptance execution requires a Linux Docker host; offline verification works on macOS"
        )
    if Path(__file__).resolve().parents[1] != root / "src/sieve_tool":
        raise ValueError(
            "Use the selected checkout's editable installation: uv sync --locked"
        )
    return verify_revision(root, expected)


def _profile(root: Path, suite: Suite) -> ControllerConfig:
    profile = load_controller_config(root / "controllers" / suite.controller)
    if (
        profile.commit != suite.controller_revision
        or profile.kubernetes_version != suite.kubernetes_version
    ):
        raise ValueError(
            "Acceptance controller/Kubernetes pins differ from the frozen suite"
        )

    return profile


def _settings(common: CommonConfig) -> None:
    defaults = json.loads(
        files("sieve_tool.resources").joinpath("defaults.json").read_text()
    )
    adjustable = {
        "container_registry",
        "process_timeouts",
        "workload_command_wait_timeout",
        "workload_conditional_wait_timeout",
    }
    for name, value in asdict(common).items():
        if name not in adjustable and value != defaults[name]:
            raise ValueError(
                f"Acceptance requires the default {name}; only registry/timeouts may be overridden"
            )


def _cli(root: Path, settings: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        "sieve_tool",
        "--repo",
        str(root),
        "--config",
        str(settings),
    ]


def _inspect(references: list[str]) -> dict[str, str]:
    return {
        ref: run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", ref],
            operation="inspect acceptance image",
            capture_output=True,
        ).stdout.strip()
        for ref in references
    }


def _cleanup_grace(policy: dict[str, int | float]) -> float:
    return float(policy["cleanup"] + policy["terminate"] + policy["request"])


def _single_budget(
    common: CommonConfig, overrides: dict[str, int | float] | None = None
) -> float:
    policy = validate_timeouts({**common.process_timeouts, **(overrides or {})})
    return sum(
        float(policy[name])
        for name in (
            "compile",
            "cluster",
            "workload",
            "cleanup",
            "terminate",
            "command",
        )
    )


def build_images(
    root: Path, expected: str, registry: str, output: Path, common: CommonConfig
) -> None:
    source = _source(root, expected)
    _settings(common)
    suite = load_suite(root)
    _profile(root, suite)
    if output.exists():
        raise FileExistsError(f"Image manifest already exists: {output}")
    if output.is_relative_to(root) and not any(
        output.is_relative_to(root / name) for name in ("runs", ".cache")
    ):
        raise ValueError(
            "Image manifests inside the checkout belong under .cache/ or runs/"
        )
    inputs = build_inputs(root)
    # Validate the registry before launching any build.
    ImageManifest(
        registry=registry, built_revision=expected, build_inputs=inputs, images={}
    )
    logs = output.with_name(output.stem + "-logs")
    logs.mkdir(parents=True, exist_ok=False)
    settings = logs / "harness-config.json"
    save_json(settings, asdict(common))
    budget = sum(
        float(validate_timeouts(common.process_timeouts)[name])
        for name in ("network", "compile", "build", "cleanup", "terminate")
    )
    with execution(common.process_timeouts, logs / "commands"):
        for mode in MODES:
            for name, arguments in (
                ("node", ["--kubernetes-version", suite.kubernetes_version]),
                ("controller", ["-c", suite.controller]),
            ):
                with execution(
                    {
                        "terminate": _cleanup_grace(
                            validate_timeouts(common.process_timeouts)
                        )
                    }
                ):
                    run(
                        [
                            *_cli(root, settings),
                            "build",
                            *arguments,
                            "-m",
                            mode,
                            "-r",
                            registry,
                        ],
                        operation=f"build acceptance {name} {mode}",
                        timeout=budget,
                        cwd=root,
                        output=logs / f"{name}-{mode}.log",
                    )

        if build_inputs(root) != inputs:
            raise ValueError("Image build inputs changed while building")
        verify_revision(root, source["revision"])
        images = _inspect(image_references(suite, registry))
    manifest = ImageManifest(
        registry=registry, built_revision=expected, build_inputs=inputs, images=images
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    save_json(output, manifest.model_dump(mode="json"))


def _no_cluster() -> None:
    clusters = run(
        ["kind", "get", "clusters"],
        operation="check acceptance cluster ownership",
        capture_output=True,
    ).stdout.splitlines()
    if "kind" in clusters:
        raise ValueError(
            "A kind cluster already exists; acceptance will not delete an unowned cluster"
        )


def _stage(
    record: RunRecord,
    output: Path,
    name: str,
    command: list[str],
    budget: float,
    root: Path,
    env: dict[str, str],
    cleanup_grace: float,
) -> None:
    phase = Phase(name=name, log=name + ".log")
    record.phases.append(phase)
    save_json(output / "run.json", record.model_dump(mode="json"))
    try:
        with execution({"terminate": cleanup_grace}):
            result = run(
                command,
                operation="acceptance " + name,
                timeout=budget,
                cwd=root,
                env=env,
                output=output / phase.log,
                check=False,
            )
        phase.returncode = result.returncode
        if result.returncode:
            raise ProcessError(
                "acceptance " + name, result.returncode, output / phase.log
            )
    finally:
        save_json(output / "run.json", record.model_dump(mode="json"))


def execute(
    root: Path,
    expected: str,
    images_path: Path,
    selection: Selection,
    output: Path,
    common: CommonConfig,
) -> dict[str, object]:
    source = _source(root, expected)
    _settings(common)
    suite = load_suite(root)
    profile = _profile(root, suite)
    overrides = profile.test_setting.get(suite.workload, {}).get("process_timeouts", {})
    budget = _single_budget(common, overrides)
    grace = _cleanup_grace(validate_timeouts({**common.process_timeouts, **overrides}))
    manifest = read_artifact(images_path, IMAGE_ADAPTER)
    references = image_references(suite, manifest.registry)
    if set(manifest.images) != set(references) or manifest.build_inputs != build_inputs(
        root
    ):
        raise ValueError(
            "Image manifest does not match this checkout's image build inputs; rebuild the acceptance images"
        )
    if output.is_relative_to(root) and not output.is_relative_to(root / "runs"):
        raise ValueError("Acceptance output inside the checkout belongs under runs/")
    output.mkdir(parents=True, exist_ok=False)
    runtime_registry = "sieve-acceptance-run-" + uuid4().hex
    record = RunRecord(
        selection=selection,
        source=source,
        images=manifest,
        runtime_registry=runtime_registry,
    )
    save_json(output / "run.json", record.model_dump(mode="json"))
    aliases: list[str] = []
    primary: BaseException | None = None
    try:
        with execution(common.process_timeouts, output / "commands"):
            _no_cluster()
            if _inspect(references) != manifest.images:
                raise ValueError("Docker image IDs do not match the supplied manifest")
            for original, alias in zip(
                references, image_references(suite, runtime_registry), strict=True
            ):
                # Immutable IDs freeze this run even if a cache tag is moved by
                # a concurrent build. Only these unique aliases are removed.
                aliases.append(alias)
                run(
                    ["docker", "image", "tag", manifest.images[original], alias],
                    operation="create owned acceptance image alias",
                )
            settings = output / "harness-config.json"
            save_json(settings, asdict(common))
            cli = _cli(root, settings)
            options = [
                "-c",
                suite.controller,
                "-r",
                runtime_registry,
                "--oracle-root",
                str(output / "oracles"),
            ]
            env = {"KUBECONFIG": str(output / "kubeconfig"), "GOWORK": "off"}
            cases = suite.select(selection)
            for case in cases:
                target = output / "plans" / case.relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(suite_directory(root) / case.relative_path, target)
            _stage(
                record,
                output,
                "vanilla",
                [
                    *cli,
                    "vanilla",
                    *options,
                    "-w",
                    suite.workload,
                    "--output",
                    str(output / "vanilla"),
                ],
                budget,
                root,
                env,
                grace,
            )
            verify_workload(
                output / "vanilla" / suite.controller / suite.workload / "vanilla",
                capture_ca=False,
            )
            _no_cluster()
            _stage(
                record,
                output,
                "learn",
                [
                    *cli,
                    "learn",
                    *options,
                    "-w",
                    suite.workload,
                    "--output",
                    str(output / "learn"),
                ],
                2 * budget,
                root,
                env,
                grace,
            )
            for mode in ("learn", "learn_prev"):
                verify_workload(
                    output / "learn" / suite.controller / suite.workload / mode
                )
            _no_cluster()
            for case in cases:
                _stage(
                    record,
                    output,
                    case.name,
                    [
                        *cli,
                        "test",
                        *options,
                        "-p",
                        str(output / "plans" / case.relative_path),
                        "--output",
                        str(output / "test"),
                    ],
                    budget,
                    root,
                    env,
                    grace,
                )
                verify_case(output, suite, case)
                _no_cluster()
            verify_revision(root, expected)
    except BaseException as error:
        primary = error
    finally:
        with execution(common.process_timeouts, output / "commands"):
            try:
                reset_cleanup_budget()
                cleanup_failure: Exception | None = None
                with cleanup_budget():
                    for alias in reversed(aliases):
                        try:
                            exists = run(
                                [
                                    "docker",
                                    "image",
                                    "ls",
                                    "--format",
                                    "{{.ID}}",
                                    "--filter",
                                    "reference=" + alias,
                                ],
                                operation="find owned acceptance image alias",
                                capture_output=True,
                                category="cleanup",
                            )
                            if not exists.stdout.strip():
                                continue
                            run(
                                ["docker", "image", "rm", alias],
                                operation="remove owned acceptance image alias",
                                category="cleanup",
                            )
                        except Exception as error:
                            if cleanup_failure is None:
                                cleanup_failure = error
                            else:
                                cleanup_failure.add_note(str(error))
                if cleanup_failure is not None:
                    raise cleanup_failure
                record.aliases_removed = True
            except BaseException as error:
                if primary is None:
                    primary = error
                else:
                    primary.add_note(f"Acceptance alias cleanup also failed: {error}")
        record.status = (
            "completed"
            if primary is None
            else "cancelled"
            if isinstance(primary, (Cancelled, KeyboardInterrupt))
            else "failed"
        )
        record.error = (
            None
            if primary is None
            else "\n".join([str(primary), *getattr(primary, "__notes__", [])])
        )
        save_json(output / "run.json", record.model_dump(mode="json"))
        if primary is None:
            try:
                report = verify(output, suite, require_done=False)
                save_json(output / "verification.json", report)
            except BaseException as error:
                primary = error
                record.status, record.error = "failed", str(error)
                save_json(output / "run.json", record.model_dump(mode="json"))
        (output / "DONE").write_text(record.status + "\n")
    if primary is not None:
        raise primary
    return report
