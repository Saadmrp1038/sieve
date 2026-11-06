"""Prune recovery is checked at the public semantic-monitor boundary."""

from copy import deepcopy

import pytest


@pytest.mark.parametrize(
    "phase,change,expired,expected,reason",
    [
        ("blocked", None, False, "satisfied", ()),
        ("blocked", "untracked", False, "violation", ("prune_inventory",)),
        ("blocked", "converging", False, "pending", ("prune_unconfirmed",)),
        ("blocked", "converging", True, "violation", ("prune_unconfirmed",)),
        ("blocked", "deleting", False, "violation", ("rejected_delete",)),
        ("recovered", None, False, "satisfied", ()),
        ("recovered", "present", False, "pending", ("prune_unconfirmed",)),
        ("recovered", "present", True, "violation", ("prune_unconfirmed",)),
        ("recovered", "policy_deleted", False, "violation", ("prune_policy",)),
        ("recovered", "replacement_parent", False, "violation", ("accepted_input",)),
    ],
)
def test_rejected_prune_stays_tracked_and_recovers_without_deleting_policy_controls(
    phase, change, expired, expected, reason
):
    from sieve_tool.testing.pruning import PruneRecoveryMonitor

    parent = {
        "metadata": {"uid": "parent-1", "generation": 1},
        "spec": {"prune": True},
    }
    victim = {"metadata": {"uid": "victim-1"}, "data": {"value": "synthetic"}}
    controls = {
        "disabled": {"metadata": {"uid": "disabled-1"}},
        "foreign": {"metadata": {"uid": "foreign-1"}},
    }
    monitor = PruneRecoveryMonitor(
        accepted=parent,
        victim=victim,
        controls=controls,
        inventory_id="default_zz-sieve-prune-victim__ConfigMap",
        revision="main@sha1:2222222222222222222222222222222222222222",
    )
    observed = deepcopy(parent)
    observed["status"] = {
        "lastAttemptedRevision": "main@sha1:2222222222222222222222222222222222222222",
        "lastAppliedRevision": "main@sha1:2222222222222222222222222222222222222222",
        "conditions": [
            {
                "type": "Ready",
                "status": "False" if phase == "blocked" else "True",
                "reason": "PruneFailed"
                if phase == "blocked"
                else "ReconciliationSucceeded",
                "observedGeneration": 1,
            }
        ],
        "inventory": {
            "entries": [{"id": "default_zz-sieve-prune-victim__ConfigMap", "v": "v1"}]
            if phase == "blocked"
            else []
        },
    }
    current = deepcopy(victim) if phase == "blocked" or change == "present" else None
    if change == "untracked":
        observed["status"]["inventory"]["entries"] = []
    if change == "converging":
        observed["status"]["conditions"] = []
    if change == "deleting":
        current["metadata"]["deletionTimestamp"] = "2026-11-02T12:00:00Z"
    if change == "policy_deleted":
        controls = {**controls, "foreign": None}
    if change == "replacement_parent":
        observed["metadata"]["uid"] = "replacement"
    verdict = monitor.observe(
        phase=phase, parent=observed, victim=current, controls=controls, expired=expired
    )
    assert (verdict.status, verdict.reasons) == (expected, reason)
