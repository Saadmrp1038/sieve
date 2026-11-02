"""Reusable monitors expose semantic verdicts through a public interface."""

import copy

import pytest


@pytest.fixture(params=["ExternalSecret", "Certificate"])
def latest(request):
    parent = {
        "kind": request.param,
        "metadata": {"name": "example", "uid": "parent-1", "generation": 2},
        "spec": {"selection": "b"},
    }
    child = {
        "metadata": {
            "name": "example",
            "uid": "child-1",
            "ownerReferences": [
                {
                    "uid": "parent-1",
                    "name": "example",
                    "kind": request.param,
                    "controller": True,
                }
            ],
        }
    }
    return parent, child


def test_latest_owned_ready_value_satisfies_the_monitor_for_both_operators(latest):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    monitor = LatestStateMonitor(accepted=parent, expected_value="b")
    assert (
        monitor.observe(
            parent=copy.deepcopy(parent),
            child=child,
            effective_value="b",
            ready_generation=2,
        ).status
        == "satisfied"
    )


def test_replacement_parent_cannot_inherit_the_old_inputs_success(latest):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    monitor = LatestStateMonitor(accepted=parent, expected_value="b")
    replacement = copy.deepcopy(parent)
    replacement["metadata"]["uid"] = "parent-2"
    verdict = monitor.observe(
        parent=replacement, child=child, effective_value="b", ready_generation=2
    )
    assert verdict.status == "violation"
    assert verdict.reasons == ("parent_identity",)


@pytest.mark.parametrize(
    "field,value",
    [("uid", "other"), ("kind", "Other"), ("name", "other"), ("controller", False)],
)
def test_child_must_be_controlled_by_the_accepted_parent(latest, field, value):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    child["metadata"]["ownerReferences"][0][field] = value
    verdict = LatestStateMonitor(accepted=parent, expected_value="b").observe(
        parent=parent, child=child, effective_value="b", ready_generation=2
    )
    assert verdict.status == "violation"
    assert verdict.reasons == ("child_ownership",)


@pytest.mark.parametrize(
    "observation", ["missing-child", "old-ready-generation", "not-ready"]
)
def test_incomplete_reconciliation_is_pending_not_a_passing_or_failed_property(
    latest, observation
):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    verdict = LatestStateMonitor(accepted=parent, expected_value="b").observe(
        parent=parent,
        child=None if observation == "missing-child" else child,
        effective_value="a",
        ready_generation=1 if observation == "old-ready-generation" else None,
    )
    assert verdict.status == "pending"


def test_current_ready_cannot_hide_an_old_effective_value(latest):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    verdict = LatestStateMonitor(accepted=parent, expected_value="b").observe(
        parent=parent, child=child, effective_value="a", ready_generation=2
    )
    assert verdict.status == "violation"
    assert verdict.reasons == ("ready_value",)


@pytest.mark.parametrize("changed", ["generation", "spec"])
def test_observed_parent_must_match_the_latest_accepted_input(latest, changed):
    from sieve_tool.testing.semantic import LatestStateMonitor

    parent, child = latest
    observed = copy.deepcopy(parent)
    if changed == "generation":
        observed["metadata"]["generation"] = 1
    else:
        observed["spec"] = {"selection": "a"}
    verdict = LatestStateMonitor(accepted=parent, expected_value="b").observe(
        parent=observed, child=child, effective_value="b", ready_generation=2
    )
    assert verdict.status == "violation"
    assert verdict.reasons == ("accepted_input",)


def test_stability_requires_an_uninterrupted_interval_of_satisfied_observations():
    from sieve_tool.testing.semantic import SemanticVerdict, StabilityWindow

    window = StabilityWindow(seconds=2)
    healthy = SemanticVerdict("satisfied")
    assert not window.observe(healthy, now_ns=0)
    assert not window.observe(healthy, now_ns=1_900_000_000)
    assert not window.observe(SemanticVerdict("pending"), now_ns=2_000_000_000)
    assert not window.observe(healthy, now_ns=3_000_000_000)
    assert window.observe(healthy, now_ns=5_000_000_000)


@pytest.mark.parametrize("seconds", [-1, float("inf"), float("nan"), True])
def test_stability_rejects_invalid_durations(seconds):
    from sieve_tool.testing.semantic import StabilityWindow

    with pytest.raises(ValueError, match="duration"):
        StabilityWindow(seconds=seconds)


def test_a_replacement_controller_starts_a_fresh_stability_interval():
    from sieve_tool.testing.semantic import SemanticVerdict, StabilityWindow

    window = StabilityWindow(seconds=2)
    healthy = SemanticVerdict("satisfied")
    assert not window.observe(healthy, now_ns=0, epoch="original-controller")
    assert not window.observe(
        healthy, now_ns=1_900_000_000, epoch="replacement-controller"
    )
    assert not window.observe(
        healthy, now_ns=2_100_000_000, epoch="replacement-controller"
    )
    assert window.observe(healthy, now_ns=3_900_000_000, epoch="replacement-controller")
