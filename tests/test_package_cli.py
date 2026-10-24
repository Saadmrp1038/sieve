"""Package boundaries and user-visible failure behavior."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
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
    config = load_controller_config(profile)
    assert (checkout / shlex.split(config.test_command)[1]).is_file()
    assert cli.main(command) == 1
    assert Path.cwd() == caller
    assert not (caller / "controllers").exists()


@pytest.mark.parametrize(
    "settings, message",
    [
        ({"typo": True}, "Unknown Sieve settings"),
        ({"generate_debugging_information_enabled": []}, "must be bool"),
        ({"workload_command_wait_timeout": True}, "must be int"),
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
    context = SimpleNamespace(postprocess=False)
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
    assert len(profiles) == 12
    for path in profiles:
        config = load_controller_config(path.parent)
        assert (ROOT / shlex.split(config.test_command)[1]).is_file(), path
        assert (ROOT / config.controller_deployment_file_path).is_file(), path
        assert (path.parent / "build/build.sh").is_file(), path
        assert (path.parent / "deploy/deploy.sh").is_file(), path


def test_missing_explicit_config_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_common_config(tmp_path / "missing.json")
