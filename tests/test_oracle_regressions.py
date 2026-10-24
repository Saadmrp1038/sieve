import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sieve_common.common import SIEVE_LEARN_VALUE_MASK
from sieve_oracle.checker_common import (
    canonicalize_controller_related_list,
    second_pass_learn_trim,
    test_run_validation as validate_test_run,
)
from sieve_oracle import liveness_checker


class OracleRegressionTests(unittest.TestCase):
    def test_nondeterministic_keys_are_learned_in_either_baseline_order(self):
        first = {"stable": {"spec": {"fixed": 1, "changing": "a", "first": 2}}}
        second = {
            "stable": {"spec": {"fixed": 1, "changing": "b", "second": 3}},
            "extra": {"value": 4},
        }
        originals = copy.deepcopy((first, second))
        for a, b in ((first, second), (second, first)):
            with self.subTest(order=list(a)):
                learned = second_pass_learn_trim(a, b)
                self.assertEqual(set(learned), set(a))
                if "extra" in a:
                    self.assertEqual(learned["extra"], SIEVE_LEARN_VALUE_MASK)
                self.assertEqual(
                    learned["stable"]["spec"],
                    {
                        "fixed": 1,
                        "changing": SIEVE_LEARN_VALUE_MASK,
                        "first": SIEVE_LEARN_VALUE_MASK,
                        "second": SIEVE_LEARN_VALUE_MASK,
                    },
                )
        self.assertEqual((first, second), originals)

    def test_stable_resource_is_not_masked(self):
        state = {"secret/default/example": {"data": {"marker": "expected"}}}
        self.assertEqual(second_pass_learn_trim(state, state), state)

    def test_random_names_do_not_double_the_reference_count(self):
        first = {"pod/default/random-a": {"uid": "a"}}
        second = {"pod/default/random-b": {"uid": "b"}}
        for a, b in ((first, second), (second, first)):
            self.assertEqual(
                second_pass_learn_trim(a, b), {next(iter(a)): SIEVE_LEARN_VALUE_MASK}
            )

    def test_controller_family_includes_both_baselines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, family in (
                ("learn", ["pod/default/second"]),
                ("learn_prev", ["pod/default/first"]),
            ):
                (root / name).mkdir()
                (root / name / "controller_family.json").write_text(json.dumps(family))
            result = canonicalize_controller_related_list(
                SimpleNamespace(result_dir=str(root / "learn"))
            )
            self.assertEqual(result, ["pod/default/first", "pod/default/second"])

    def test_controller_pods_are_excluded_from_counts_but_operand_pods_are_checked(
        self,
    ):
        reference = {
            "pod/default/controller-a": SIEVE_LEARN_VALUE_MASK,
            "pod/default/controller-b": SIEVE_LEARN_VALUE_MASK,
            "pod/default/operand-a": SIEVE_LEARN_VALUE_MASK,
        }
        current = {
            "pod/default/controller-c": {"uid": "c"},
            "pod/default/operand-b": {"uid": "b"},
        }
        context = SimpleNamespace(
            test_workload="test",
            controller_config=SimpleNamespace(
                end_state_checker_mask={}, custom_resource_definitions=[]
            ),
            common_config=SimpleNamespace(field_key_mask={}, field_path_mask={}),
        )
        with (
            patch.object(
                liveness_checker, "get_canonicalized_state", return_value=reference
            ),
            patch.object(liveness_checker, "get_testing_state", return_value=current),
            patch.object(
                liveness_checker,
                "get_reference_controller_related_list",
                return_value=["pod/default/controller-a", "pod/default/controller-b"],
            ),
            patch.object(
                liveness_checker,
                "get_current_controller_related_list",
                return_value=["pod/default/controller-c"],
            ),
        ):
            self.assertEqual(liveness_checker.compare_states(context), [])
            del current["pod/default/operand-b"]
            messages = liveness_checker.compare_states(context)
            self.assertEqual(len(messages), 1)
            self.assertIn("fewer objects than reference", messages[0])
            self.assertIn("operand-a", messages[0])

    def validate(self, server, actions):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sieve-server.log").write_text(server)
            (root / "workload.log").write_text("FINISH-SIEVE-TEST\n")
            return validate_test_run(
                SimpleNamespace(
                    result_dir=directory,
                    test_plan_content={"actions": actions},
                )
            )

    def test_completed_actions_without_blocked_read_are_incomplete_injection(self):
        actions = [
            {
                "actionType": "pauseController",
                "pauseAt": "beforeControllerRead",
                "pauseScope": "secret/default/example",
            }
        ]
        self.assertEqual(
            self.validate("Sieve test coordinator finishes all actions\n", actions),
            (False, True),
        )

    def test_block_and_release_must_match_the_requested_scope(self):
        action = {
            "actionType": "pauseController",
            "pauseAt": "beforeControllerRead",
            "pauseScope": "secret/default/example",
        }
        prefix = "Sieve test coordinator finishes all actions\n"
        pause = "time file.go:1: Pause controller at beforeControllerRead secret/default/example\n"
        resume = "time file.go:1: End pause controller at beforeControllerRead secret/default/example\n"
        self.assertEqual(self.validate(prefix + pause + resume, [action]), (True, True))
        self.assertEqual(self.validate(prefix + pause, [action]), (False, True))
        self.assertEqual(
            self.validate(
                (prefix + pause + resume).replace(
                    "secret/default/example", "stringsecret/default/example"
                ),
                [action],
            ),
            (False, True),
        )
        self.assertEqual(
            self.validate(prefix + pause + resume, [action, action]), (False, True)
        )

    def test_restart_does_not_require_a_read_pause(self):
        self.assertEqual(
            self.validate(
                "Sieve test coordinator finishes all actions\n",
                [{"actionType": "restartController"}],
            ),
            (True, True),
        )


if __name__ == "__main__":
    unittest.main()
