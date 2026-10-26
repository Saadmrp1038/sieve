"""Package boundaries and user-visible failure behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sieve_tool import cli
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.paths import repository
from sieve_tool.runtime import runner

ROOT = Path(__file__).resolve().parents[1]


def test_installed_help_from_outside_checkout_without_gopath(tmp_path):
    environment = dict(os.environ)
    environment.pop("GOPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "sieve_tool", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "controller" in result.stdout
    assert not list(tmp_path.iterdir())


def test_explicit_repository_scaffold_and_cwd_restoration(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    (checkout / "controllers").mkdir(parents=True)
    (checkout / "go.work").touch()
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    command = ["--repo", str(checkout), "controller", "init", "new-operator"]
    assert cli.main(command) == 0
    profile = checkout / "controllers/new-operator"
    config = json.loads((profile / "config.json").read_text())
    assert (checkout / config["test_command"][1]).is_file()
    with pytest.raises(ValueError, match="commit"):
        load_controller_config(profile)
    assert cli.main(command) == 1
    assert Path.cwd() == caller
    assert not (caller / "controllers").exists()


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"typo": True}, "Unknown Sieve settings"),
        ({"generate_debugging_information_enabled": []}, "valid boolean"),
        ({"workload_command_wait_timeout": True}, "valid integer"),
    ],
)
def test_invalid_override_fails(tmp_path, settings, message):
    override = tmp_path / "config.json"
    override.write_text(json.dumps(settings))
    with pytest.raises(ValueError, match=message):
        get_common_config(override)


def test_packaged_defaults_and_boolean_override(tmp_path):
    override = tmp_path / "config.json"
    override.write_text('{"generate_debugging_information_enabled": false}')
    assert get_common_config(override).generate_debugging_information_enabled is False
    assert get_common_config(override).namespace == "default"


@pytest.mark.parametrize(
    "failure", ["no_exception", "injection_completed", "workload_completed", "alarm"]
)
def test_cli_returns_failure_for_incomplete_or_alarming_test(tmp_path, failure):
    values = dict(
        no_exception=True,
        injection_completed=True,
        workload_completed=True,
        common_errors=[],
        end_state_errors=[],
        history_errors=[],
    )
    if failure == "alarm":
        values["end_state_errors"] = ["raw alarm"]
    else:
        values[failure] = False
    result = SimpleNamespace(**values)
    plan = tmp_path / "plan.yaml"
    plan.touch()
    with (
        patch.object(runner, "run", return_value=(result, SimpleNamespace())),
        patch.object(runner, "save_run_result"),
    ):
        assert (
            cli.main(
                [
                    "--repo",
                    str(ROOT),
                    "test",
                    "-c",
                    "kubernetes-secret-generator",
                    "-p",
                    str(plan),
                ]
            )
            == 1
        )


def test_failed_setup_cleans_cluster_and_keeps_original_error():
    context = SimpleNamespace(postprocess=False, mode="test", build_oracle=False)
    with (
        patch.object(runner, "prepare_test_plan"),
        patch.object(runner, "setup_cluster", side_effect=RuntimeError("setup broke")),
        patch.object(runner, "teardown_cluster") as teardown,
    ):
        result = runner.run_test(context)
    teardown.assert_called_once_with()
    assert not result.no_exception
    assert "setup broke" in result.exception_message


def test_existing_evidence_is_not_deleted(tmp_path):
    evidence = tmp_path / "result"
    evidence.mkdir()
    marker = evidence / "raw.log"
    marker.write_text("evidence")
    with pytest.raises(FileExistsError):
        runner.prepare_test_plan(SimpleNamespace(result_dir=str(evidence)))
    assert marker.read_text() == "evidence"


def test_all_controller_profiles_point_to_moved_assets():
    profiles = list((ROOT / "controllers").glob("*/config.json"))
    assert profiles
    for path in profiles:
        config = load_controller_config(path.parent)
        assert (ROOT / config.test_command[1]).is_file(), path
        assert (ROOT / config.controller_deployment_file_path).is_file(), path
        assert (path.parent / "build/build.sh").is_file(), path
        assert (path.parent / "deploy/deploy.sh").is_file(), path


def test_missing_explicit_config_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_common_config(tmp_path / "missing.json")


def test_workload_child_receives_selected_configuration(tmp_path):
    settings = tmp_path / "harness-config.json"
    settings.write_text(
        '{"workload_command_wait_timeout": 123, "generate_debugging_information_enabled": false}'
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from sieve_tool.common.config import get_common_config; c = get_common_config(); assert c.workload_command_wait_timeout == 123; assert c.generate_debugging_information_enabled is False",
        ],
        cwd=tmp_path,
        env={**os.environ, "SIEVE_HARNESS_CONFIG": str(settings)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_configmap_carries_json_without_changing_the_yaml_plan(tmp_path):
    import yaml

    from sieve_tool.runtime.cluster import create_configmap

    plan = tmp_path / "plan.yaml"
    text = "actions: []\nannotatedReconcileStackFrame: []\ncrdList: [stringsecret]\n"
    plan.write_text(text)
    manifest = yaml.safe_load(Path(create_configmap(str(plan))).read_text())
    payload = json.loads(manifest["data"]["sieveTestPlan"])
    assert payload == yaml.safe_load(text)
    assert plan.read_text() == text


def test_library_run_resolves_assets_without_switching_caller_directory(
    tmp_path, monkeypatch
):
    checkout = tmp_path / "repo"
    profile = checkout / "controllers/kubernetes-secret-generator"
    profile.mkdir(parents=True)
    (profile / "config.json").write_text(
        (ROOT / "controllers/kubernetes-secret-generator/config.json").read_text()
    )
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    with repository(checkout), patch.object(runner, "run_test", return_value=None):
        _, context = runner.run(
            "controllers/kubernetes-secret-generator",
            "create-update-recreate",
            "results",
            "vanilla",
            str(tmp_path / "plan.yaml"),
            "registry",
            True,
            False,
        )
    assert Path(context.controller_config_dir) == profile
    assert Path(context.result_root_dir) == caller / "results"
    assert Path.cwd() == caller
