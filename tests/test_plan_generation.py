"""Characterize schedule generation independently of cluster execution."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sieve_tool.faults.intermediate_state import (
    generate_intermediate_state_test_plan_for_annotated_api_invocation,
    generate_intermediate_state_test_plan_for_controller_write,
)
from sieve_tool.faults.stale_state import generate_stale_state_test_plan
from sieve_tool.faults.unobserved_state import (
    causality_hear_filtering_pass,
    generate_unobserved_state_test_plan,
    overwrite_filtering_pass,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = list(
    yaml.safe_load_all((ROOT / "fixtures/plans/generation/cases.yaml").read_text())
)


@pytest.fixture
def context():
    return SimpleNamespace(
        test_workload="exercise",
        controller_config=SimpleNamespace(
            controller_pod_label="operator", custom_resource_definitions=["example"]
        ),
        common_config=SimpleNamespace(
            leading_api="kind-control-plane", following_api="kind-control-plane3"
        ),
    )


def event(kind):
    return SimpleNamespace(
        etype=kind,
        rtype="secret",
        namespace="default",
        name="generated",
        signature_counter=2,
        reconcile_fun="example.(*Reconciler).Reconcile",
        slim_prev_obj_map={"data": {"value": "before"}},
        slim_cur_obj_map={"data": {"value": "after"}},
    )


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda c: "-".join(c[k] for k in ["family", "event", "timing"] if k in c),
)
def test_generation_preserves_pre_refactor_schedules(context, case):
    family = case["family"]
    if family == "intermediate":
        plan = generate_intermediate_state_test_plan_for_controller_write(
            context, event(case["event"])
        )
    elif family == "annotated":
        invocation = SimpleNamespace(
            module="example/api",
            file_path="client.go",
            recv_type="*Client",
            fun_name="Create",
            signature_counter=2,
            reconcile_fun="example.(*Reconciler).Reconcile",
        )
        plan = generate_intermediate_state_test_plan_for_annotated_api_invocation(
            context, invocation
        )
    elif family == "unobserved":
        plan = generate_unobserved_state_test_plan(context, event(case["event"]))
    else:
        plan = generate_stale_state_test_plan(
            context, event(case["event"]), event("Create"), case["timing"]
        )
    assert plan.to_dict() == case["plan"]


def test_unobserved_candidates_require_an_overwritten_state():
    stable = SimpleNamespace(content=SimpleNamespace(cancelled_by=set()))
    overwritten = SimpleNamespace(content=SimpleNamespace(cancelled_by={2}))
    assert overwrite_filtering_pass([stable, overwritten]) == [overwritten]


def test_causality_pruning_keeps_receipts_that_can_affect_reconciliation():
    unrelated = SimpleNamespace(out_inter_reconciler_edges=[])
    related = SimpleNamespace(out_inter_reconciler_edges=[object()])
    assert causality_hear_filtering_pass([unrelated, related]) == [related]
