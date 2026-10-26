"""Exercise comparison contracts without filesystem or cluster dependencies."""

import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.oracle.liveness_checker import compare_states
from sieve_tool.oracle.oracle import check
from sieve_tool.oracle.policy import ComparisonPolicy
from sieve_tool.oracle.safety_checker import (
    apply_safety_checker,
    compare_history_digests,
)
from sieve_tool.oracle.validation import (
    controller_panic_checker,
    workload_error_checker,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures/oracle-contracts"


@pytest.mark.parametrize("path", sorted(CORPUS.glob("*.json.gz")), ids=lambda p: p.stem)
def test_frozen_comparisons_need_no_io_and_do_not_mutate_inputs(path):
    group = json.loads(gzip.decompress(path.read_bytes()))
    common = get_common_config()
    controller = load_controller_config(
        ROOT / "controllers/kubernetes-secret-generator"
    )
    for name, value in group.get("common", {}).items():
        setattr(common, name, value)
    for name, value in group.get("controller", {}).items():
        setattr(controller, name, value)
    policy = ComparisonPolicy(group["workload"], common, controller)
    original = copy.deepcopy(group)
    original_policy = copy.deepcopy(policy)
    reference = group["expected"]["reference"]
    with (
        patch("builtins.open", side_effect=AssertionError("unexpected file read")),
        patch.object(Path, "open", side_effect=AssertionError("unexpected file read")),
        patch("builtins.print", side_effect=AssertionError("unexpected output")),
    ):
        for case in group["tests"]:
            artifacts = case["artifacts"]
            expected = group["expected"]["tests"][case["name"]]["verdict"]
            assert (
                compare_states(
                    reference["state.json"],
                    artifacts["state.json"],
                    policy,
                    reference["controller_family.json"],
                    artifacts["controller_family.json"],
                )
                == expected["end_state_errors"]
            )
            actions = case["plan"]["actions"] or []
            if not (
                actions
                and actions[0]["actionType"] == "pauseController"
                and actions[0]["pauseAt"] == "beforeControllerRead"
            ):
                assert (
                    compare_history_digests(
                        reference["event.json"],
                        artifacts["event.json"],
                        artifacts["controller_family.json"],
                        policy,
                    )
                    == expected["history_errors"]
                )
    assert group == original
    assert policy == original_policy


def test_custom_checker_runs_only_when_all_resources_exist_and_preserves_order():
    first, second = "secret/default/first", "secret/default/second"
    history = [
        {"number": 0, "etype": "ADDED", "key": first, "state": "1"},
        {
            "number": 1,
            "etype": "ADDED",
            "key": "secret/default/unrelated",
            "state": "0",
        },
        {"number": 2, "etype": "ADDED", "key": second, "state": "2"},
        {"number": 3, "etype": "MODIFIED", "key": first, "state": "2"},
        {"number": 4, "etype": "DELETED", "key": second, "state": "null"},
        {"number": 5, "etype": "ADDED", "key": second, "state": "3"},
    ]
    original = copy.deepcopy(history)
    observed = []

    def matching(state):
        observed.append((state[first], state[second]))
        return state[first] == state[second]

    assert apply_safety_checker(history, [first, second], "matching", matching) == [
        "Safety violation: checker matching failed on state 2 in history.json",
        "Safety violation: checker matching failed on state 5 in history.json",
    ]
    assert observed == [("1", "2"), ("2", "2"), ("2", "3")]
    assert history == original


def test_custom_checker_exception_remains_a_harness_error():
    def broken(state):
        raise RuntimeError("checker failed")

    with pytest.raises(RuntimeError, match="checker failed"):
        apply_safety_checker(
            [
                {
                    "number": 0,
                    "etype": "ADDED",
                    "key": "secret/default/one",
                    "state": "{}",
                }
            ],
            ["secret/default/one"],
            "broken",
            broken,
        )


def test_disabled_checks_do_not_load_unselected_artifacts(tmp_path):
    common = get_common_config()
    for name in [
        "controller_exception_check_enabled",
        "workload_error_check_enabled",
        "state_update_summary_check_enabled",
        "end_state_check_enabled",
    ]:
        setattr(common, name, False)
    (tmp_path / "sieve-server.log").write_text(
        "Sieve test coordinator finishes all actions\n"
    )
    (tmp_path / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (tmp_path / "state.json").write_text("malformed unselected artifact")
    ctx = SimpleNamespace(
        result_dir=str(tmp_path),
        oracle_dir=str(tmp_path / "absent"),
        test_workload="test",
        common_config=common,
        controller_config=load_controller_config(
            ROOT / "controllers/kubernetes-secret-generator"
        ),
        test_plan_content={"workload": "test", "actions": None},
        action_types=[],
        use_csi_driver_for_ref=False,
        use_csi_driver=False,
    )
    result = check(ctx)
    assert result.injection_completed and result.workload_completed
    assert result.to_dict()["number_errors"] == 0


def test_log_checks_preserve_blank_lines_and_literal_marker_rules():
    assert workload_error_checker(
        "\nConditional wait timeout\nFINISH-SIEVE-TEST\nignored\n", True
    ) == ["Error: "]
    assert workload_error_checker(
        "Conditional wait timeout\nFINISH-SIEVE-TEST\n", False
    ) == ["Error: Conditional wait timeout"]
    assert controller_panic_checker(
        "prefix Observed a panic: first\vstill same line\n"
    ) == ["Exception from controller: Observed a panic: first\vstill same line"]


def test_selected_custom_history_is_loaded_and_checked(tmp_path):
    common = get_common_config()
    for name in [
        "controller_exception_check_enabled",
        "workload_error_check_enabled",
        "state_update_summary_check_enabled",
        "end_state_check_enabled",
    ]:
        setattr(common, name, False)
    (tmp_path / "sieve-server.log").write_text(
        "Sieve test coordinator finishes all actions\n"
    )
    (tmp_path / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (tmp_path / "history.json").write_text(
        json.dumps(
            [
                {
                    "number": 0,
                    "etype": "ADDED",
                    "key": "secret/default/one",
                    "state": "{}",
                }
            ]
        )
    )
    ctx = SimpleNamespace(
        result_dir=str(tmp_path),
        oracle_dir=str(tmp_path / "absent"),
        test_workload="test",
        common_config=common,
        controller_config=load_controller_config(
            ROOT / "controllers/kubernetes-secret-generator"
        ),
        test_plan_content={"workload": "test", "actions": None},
        action_types=[],
        use_csi_driver_for_ref=False,
        use_csi_driver=False,
    )
    from sieve_tool.oracle.customized_safety_checker import SafetyCheckerSuite

    suite = SafetyCheckerSuite(
        ["secret/default/one"], "always-fails", lambda state: False
    )
    with patch("sieve_tool.oracle.oracle.customized_safety_checker_suites", [suite]):
        result = check(ctx)
    assert result.history_errors == [
        "Safety violation: checker always-fails failed on state 0 in history.json"
    ]
