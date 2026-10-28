"""Typed harness settings and controller integration configuration."""

import json
import os
from dataclasses import field, fields
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Literal, NotRequired, TypedDict

from pydantic import ConfigDict, Field, with_config
from pydantic.dataclasses import dataclass

from sieve_tool.paths import asset_path, repository_root


@dataclass(config=ConfigDict(strict=True, extra="forbid"))
class CommonConfig:
    container_registry: str
    namespace: str
    leading_api: str
    following_api: str
    state_update_summary_check_enabled: bool
    end_state_check_enabled: bool
    workload_error_check_enabled: bool
    controller_exception_check_enabled: bool
    state_update_summary_check_event_list: list[str]
    compress_trivial_reconcile_enabled: bool
    workload_conditional_wait_timeout: Annotated[int, Field(gt=0)]
    workload_command_wait_timeout: Annotated[int, Field(gt=0)]
    generate_debugging_information_enabled: bool
    causality_pruning_enabled: bool
    effective_updates_pruning_enabled: bool
    nondeterministic_pruning_enabled: bool
    persist_test_plans_enabled: bool
    field_key_mask: dict[str, list[list[str]]]
    field_path_mask: dict[str, list[list[str]]]
    state_update_summary_checker_mask: list[str]
    update_oracle_file_enabled: bool
    process_timeouts: dict[str, int | float]


def get_common_config(override: str | Path | None = None) -> CommonConfig:
    if override is None:
        override = os.environ.get("SIEVE_HARNESS_CONFIG")
    data = json.loads(
        files("sieve_tool.resources").joinpath("defaults.json").read_text()
    )
    explicit_override = override is not None
    if override is None:
        try:
            override = repository_root() / "configs/local.json"
        except ValueError:
            override = None
    if explicit_override and override is not None and not Path(override).is_file():
        raise FileNotFoundError(f"Sieve settings not found: {override}")
    if override is not None and Path(override).is_file():
        data.update(json.loads(Path(override).read_text()))
    expected = {field.name: field.type for field in fields(CommonConfig)}
    unknown = set(data) - set(expected)
    if unknown:
        raise ValueError("Unknown Sieve settings: " + ", ".join(sorted(unknown)))
    from sieve_tool.process import validate_timeouts

    validate_timeouts(data["process_timeouts"])
    return CommonConfig(**data)


type Mask = dict[str, dict[str, list[list[str]]]]
type Nonempty = Annotated[str, Field(min_length=1)]


@with_config(ConfigDict(extra="forbid"))
class WorkloadSettings(TypedDict, total=False):
    independent_updates: bool
    write_trigger: Literal["state-diff", "occurrence"]
    generated_name_prefixes: list[
        Annotated[
            str,
            Field(
                pattern=r"^[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*/[a-z0-9][a-z0-9.-]*\*$"
            ),
        ]
    ]
    num_apiservers: Annotated[int, Field(gt=0)]
    num_workers: Annotated[int, Field(ge=0)]
    use_csi_driver: bool
    process_timeouts: dict[str, int | float]


@with_config(ConfigDict(extra="forbid"))
class InstrumentedAPI(TypedDict):
    module: Nonempty
    file_path: Nonempty
    package: Nonempty
    func_name: Nonempty
    receiver_type: str
    import_map: NotRequired[dict[str, str]]


@dataclass(config=ConfigDict(strict=True, extra="forbid"))
class ControllerConfig:
    controller_name: Nonempty
    github_link: Nonempty
    commit: Nonempty
    kubernetes_version: Nonempty
    client_go_version: Nonempty
    dockerfile_path: Nonempty
    controller_image_name: Nonempty
    test_command: Annotated[list[Nonempty], Field(min_length=1)]
    custom_resource_definitions: list[Nonempty]
    annotated_reconcile_functions: dict[Nonempty, Nonempty]
    controller_pod_label: Nonempty
    controller_deployment_file_path: Nonempty
    cherry_pick_commits: list[Nonempty] = field(default_factory=list)
    apimachinery_version: Nonempty | None = None
    go_mod: Literal["mod", "vendor"] = "mod"
    vendored_controller_runtime_path: Nonempty | None = None
    vendored_client_go_path: Nonempty | None = None
    vendored_sieve_client_path: Nonempty | None = None
    apis_to_instrument: list[InstrumentedAPI] = field(default_factory=list)
    loosen_reconciler_boundary: bool = False
    container_name: Nonempty | None = None
    test_setting: dict[str, WorkloadSettings] = field(default_factory=dict)
    end_state_checker_mask: Mask = field(default_factory=dict)
    state_update_summary_checker_mask: dict[str, list[str]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        from sieve_tool.process import validate_timeouts

        for settings in self.test_setting.values():
            validate_timeouts(settings.get("process_timeouts", {}))


def load_controller_config(controller_config_dir: str | Path) -> ControllerConfig:
    source = asset_path(controller_config_dir) / "config.json"
    try:
        data = json.loads(source.read_text())
        if not isinstance(data, dict):
            raise ValueError("controller profile must be an object")
        if "controller_name" in data:
            raise ValueError("use the profile field name, not controller_name")
        data = {
            ("controller_name" if key == "name" else key): value
            for key, value in data.items()
        }
        return ControllerConfig(**data)
    except ValueError as error:
        raise ValueError(f"Invalid controller profile {source}: {error}") from error
