"""Delivery overlap is explicit, bounded, scoped and opt-in through public plans."""

import pytest

from sieve_tool.plans import parse_plan


def test_delivery_overlap_plan_round_trips_without_changing_old_plans():
    value = {
        "workload": "metadata-labels-overlap",
        "actions": [],
        "coverageResources": ["externalsecret/default/sieve-metadata-subject"],
        "deliveryOverlap": {
            "mode": "hold",
            "resourceKey": "externalsecret/default/sieve-metadata-subject",
            "reconciler": "operator.Reconcile",
            "holdPhase": "metadata-2-change",
            "releasePhase": "metadata-3-remove",
            "timeoutSeconds": 20,
        },
    }
    assert parse_plan(value).to_dict() == value
    assert parse_plan({"workload": "old", "actions": []}).to_dict() == {
        "workload": "old",
        "actions": [],
    }


def test_delivery_overlap_rejects_ambiguous_scope_or_identity():
    import copy

    import pytest

    value = {
        "workload": "metadata-labels-overlap",
        "actions": [],
        "coverageResources": ["externalsecret/default/a"],
        "deliveryOverlap": {
            "mode": "hold",
            "resourceKey": "externalsecret/default/a",
            "reconciler": "operator.Reconcile",
            "holdPhase": "change",
            "releasePhase": "remove",
            "timeoutSeconds": 20,
        },
    }
    for changes in (
        {"resourceKey": "externalsecret/default/b"},
        {"reconciler": "operator.*"},
        {"reconciler": "operator.Reconcile\nother"},
        {"releasePhase": "change"},
        {"holdPhase": " "},
    ):
        invalid = copy.deepcopy(value)
        invalid["deliveryOverlap"].update(changes)
        with pytest.raises(ValueError):
            parse_plan(invalid)


def test_new_overlap_plan_requires_explicit_empty_actions_not_historical_null():
    import pytest

    with pytest.raises(ValueError):
        parse_plan(
            {
                "workload": "metadata-labels-overlap",
                "actions": None,
                "coverageResources": ["externalsecret/default/a"],
                "deliveryOverlap": {
                    "mode": "hold",
                    "resourceKey": "externalsecret/default/a",
                    "reconciler": "operator.Reconcile",
                    "holdPhase": "change",
                    "releasePhase": "remove",
                    "timeoutSeconds": 20,
                },
            }
        )


@pytest.mark.parametrize(
    "reconciler",
    [
        "github.com/external-secrets/external-secrets/pkg/controllers/externalsecret.(*Reconciler).Reconcile",
        "github.com/cert-manager/cert-manager/pkg/controller/certificates/trigger.(*controller).ProcessItem",
    ],
)
def test_exact_native_pointer_receiver_names_are_not_wildcards(reconciler):
    value = {
        "workload": "metadata-labels-overlap",
        "actions": [],
        "coverageResources": ["externalsecret/default/a"],
        "deliveryOverlap": {
            "mode": "hold",
            "resourceKey": "externalsecret/default/a",
            "reconciler": reconciler,
            "holdPhase": "change",
            "releasePhase": "remove",
            "timeoutSeconds": 20,
        },
    }
    assert parse_plan(value).to_dict() == value
