"""Typed harness settings and controller integration configuration."""

from dataclasses import dataclass, fields
from importlib.resources import files
import json
import os
from pathlib import Path

from sieve_tool.paths import repository_root


@dataclass
class CommonConfig:
    container_registry: str
    namespace: str
    leading_api: str
    following_api: str
    state_update_summary_check_enabled: bool
    end_state_check_enabled: bool
    workload_error_check_enabled: bool
    controller_exception_check_enabled: bool
    state_update_summary_check_event_list: list
    compress_trivial_reconcile_enabled: bool
    workload_conditional_wait_timeout: int
    workload_command_wait_timeout: int
    generate_debugging_information_enabled: bool
    causality_pruning_enabled: bool
    effective_updates_pruning_enabled: bool
    nondeterministic_pruning_enabled: bool
    persist_test_plans_enabled: bool
    field_key_mask: dict
    field_path_mask: dict
    state_update_summary_checker_mask: list
    update_oracle_file_enabled: bool


def get_common_config(override=None):
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
    if explicit_override and not Path(override).is_file():
        raise FileNotFoundError(f"Sieve settings not found: {override}")
    if override is not None and Path(override).is_file():
        data.update(json.loads(Path(override).read_text()))
    expected = {field.name: field.type for field in fields(CommonConfig)}
    unknown = set(data) - set(expected)
    if unknown:
        raise ValueError("Unknown Sieve settings: " + ", ".join(sorted(unknown)))
    for name, kind in expected.items():
        if type(data.get(name)) is not kind:
            raise ValueError(f"Setting {name} must be {kind.__name__}")
    return CommonConfig(**data)


class ControllerConfig:
    def __init__(
        self,
        controller_name,
        github_link,
        commit,
        cherry_pick_commits,
        kubernetes_version,
        client_go_version,
        apimachinery_version,
        go_mod,
        vendored_controller_runtime_path,
        vendored_client_go_path,
        vendored_sieve_client_path,
        dockerfile_path,
        apis_to_instrument,
        controller_image_name,
        test_command,
        loosen_reconciler_boundary,
        custom_resource_definitions,
        annotated_reconcile_functions,
        controller_pod_label,
        container_name,
        controller_deployment_file_path,
        test_setting,
        end_state_checker_mask,
        state_update_summary_checker_mask,
    ):
        self.controller_name = controller_name
        self.github_link = github_link
        self.commit = commit
        self.cherry_pick_commits = cherry_pick_commits
        self.kubernetes_version = kubernetes_version
        self.client_go_version = client_go_version
        self.apimachinery_version = apimachinery_version
        self.go_mod = go_mod
        self.vendored_controller_runtime_path = vendored_controller_runtime_path
        self.vendored_client_go_path = vendored_client_go_path
        self.vendored_sieve_client_path = vendored_sieve_client_path
        self.dockerfile_path = dockerfile_path
        self.apis_to_instrument = apis_to_instrument
        self.controller_image_name = controller_image_name
        self.test_command = test_command
        self.loosen_reconciler_boundary = loosen_reconciler_boundary
        self.custom_resource_definitions = custom_resource_definitions
        self.annotated_reconcile_functions = annotated_reconcile_functions
        self.controller_pod_label = controller_pod_label
        self.container_name = container_name
        self.controller_deployment_file_path = controller_deployment_file_path
        self.test_setting = test_setting
        self.end_state_checker_mask = end_state_checker_mask
        self.state_update_summary_checker_mask = state_update_summary_checker_mask


def load_controller_config(controller_config_dir):
    controller_config_path = os.path.join(controller_config_dir, "config.json")
    controller_config = json.load(open(controller_config_path))
    return ControllerConfig(
        controller_name=controller_config["name"],
        github_link=controller_config["github_link"],
        commit=controller_config["commit"],
        cherry_pick_commits=controller_config["cherry_pick_commits"]
        if "cherry_pick_commits" in controller_config
        else [],
        kubernetes_version=controller_config["kubernetes_version"],
        client_go_version=controller_config["client_go_version"],
        apimachinery_version=controller_config["apimachinery_version"]
        if "apimachinery_version" in controller_config
        else None,
        go_mod=controller_config["go_mod"] if "go_mod" in controller_config else "mod",
        vendored_controller_runtime_path=controller_config[
            "vendored_controller_runtime_path"
        ]
        if "vendored_controller_runtime_path" in controller_config
        else None,
        vendored_client_go_path=controller_config["vendored_client_go_path"]
        if "vendored_client_go_path" in controller_config
        else None,
        vendored_sieve_client_path=controller_config["vendored_sieve_client_path"]
        if "vendored_sieve_client_path" in controller_config
        else None,
        dockerfile_path=controller_config["dockerfile_path"],
        apis_to_instrument=controller_config["apis_to_instrument"]
        if "apis_to_instrument" in controller_config
        else [],
        controller_image_name=controller_config["controller_image_name"],
        test_command=controller_config["test_command"],
        loosen_reconciler_boundary=controller_config["loosen_reconciler_boundary"]
        if "loosen_reconciler_boundary" in controller_config
        else False,
        custom_resource_definitions=controller_config["custom_resource_definitions"],
        annotated_reconcile_functions=controller_config[
            "annotated_reconcile_functions"
        ],
        controller_pod_label=controller_config["controller_pod_label"],
        container_name=controller_config["container_name"]
        if "container_name" in controller_config
        else None,
        controller_deployment_file_path=controller_config[
            "controller_deployment_file_path"
        ],
        test_setting=controller_config["test_setting"]
        if "test_setting" in controller_config
        else {},
        end_state_checker_mask=controller_config["end_state_checker_mask"]
        if "end_state_checker_mask" in controller_config
        else {},
        state_update_summary_checker_mask=controller_config[
            "state_update_summary_checker_mask"
        ]
        if "state_update_summary_checker_mask" in controller_config
        else {},
    )
