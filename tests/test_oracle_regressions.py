import copy
import unittest
from types import SimpleNamespace

from sieve_tool.common.common import SIEVE_LEARN_VALUE_MASK
from sieve_tool.oracle import liveness_checker
from sieve_tool.oracle.learning import (
    canonicalize_controller_related_list,
    second_pass_learn_trim,
)
from sieve_tool.oracle.policy import ComparisonPolicy
from sieve_tool.oracle.validation import validate_execution
from sieve_tool.plans import parse_plan


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

    def test_sparse_dictionaries_are_learned_per_field(self):
        first = {"secret/default/example": {"data": {"first": "a"}}}
        second = {"secret/default/example": {"data": {"second": "b"}}}
        expected = {
            "secret/default/example": {
                "data": {
                    "first": SIEVE_LEARN_VALUE_MASK,
                    "second": SIEVE_LEARN_VALUE_MASK,
                }
            }
        }
        self.assertEqual(second_pass_learn_trim(first, second), expected)

    def test_list_positions_are_learned_when_values_swap(self):
        first = {"service/default/example": {"spec": {"ports": [80, 443]}}}
        second = {"service/default/example": {"spec": {"ports": [443, 80]}}}
        self.assertEqual(
            second_pass_learn_trim(first, second),
            {
                "service/default/example": {
                    "spec": {"ports": [SIEVE_LEARN_VALUE_MASK, SIEVE_LEARN_VALUE_MASK]}
                }
            },
        )

    def test_random_names_do_not_double_the_reference_count(self):
        first = {"pod/default/random-a": {"uid": "a"}}
        second = {"pod/default/random-b": {"uid": "b"}}
        for a, b in ((first, second), (second, first)):
            self.assertEqual(
                second_pass_learn_trim(a, b), {next(iter(a)): SIEVE_LEARN_VALUE_MASK}
            )

    def test_controller_family_includes_both_baselines(self):
        self.assertEqual(
            canonicalize_controller_related_list(
                ["pod/default/first"], ["pod/default/second"]
            ),
            ["pod/default/first", "pod/default/second"],
        )

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
        policy = ComparisonPolicy(
            "test", context.common_config, context.controller_config
        )
        reference_family = ["pod/default/controller-a", "pod/default/controller-b"]
        current_family = ["pod/default/controller-c"]
        self.assertEqual(
            liveness_checker.compare_states(
                reference, current, policy, reference_family, current_family
            ),
            [],
        )
        del current["pod/default/operand-b"]
        messages = liveness_checker.compare_states(
            reference, current, policy, reference_family, current_family
        )
        self.assertEqual(len(messages), 1)
        self.assertIn("fewer objects than reference", messages[0])
        self.assertIn("operand-a", messages[0])

    def validate(self, server, actions):
        actions = copy.deepcopy(actions)
        for action in actions:
            action["trigger"] = {
                "expression": "t",
                "definitions": [
                    {
                        "triggerName": "t",
                        "condition": {"conditionType": "onTimeout", "timeoutValue": 1},
                    }
                ],
            }
            if action["actionType"] == "restartController":
                action["controllerLabel"] = "test"
        plan = parse_plan({"workload": "test", "actions": actions})
        return validate_execution(server, "FINISH-SIEVE-TEST\n", plan)

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
