"""Semantic obligations for a rejected Flux prune and subsequent recovery."""

from copy import deepcopy
from typing import Literal

from sieve_tool.testing.child_repair import Resource
from sieve_tool.testing.semantic import SemanticVerdict


class PruneRecoveryMonitor:
    def __init__(
        self,
        *,
        accepted: Resource,
        victim: Resource,
        controls: dict[str, Resource],
        inventory_id: str,
        revision: str,
    ) -> None:
        self.accepted = deepcopy(accepted)
        self.victim = deepcopy(victim)
        self.controls = deepcopy(controls)
        self.inventory_id = inventory_id
        self.revision = revision

    @staticmethod
    def _same_resource(expected: Resource, actual: Resource | None) -> bool:
        return (
            actual is not None
            and all(
                actual.get("metadata", {}).get(field)
                == expected.get("metadata", {}).get(field)
                for field in ("uid", "labels", "annotations", "deletionTimestamp")
            )
            and actual.get("data") == expected.get("data")
        )

    def observe(
        self,
        *,
        phase: Literal["blocked", "recovered"],
        parent: Resource | None,
        victim: Resource | None,
        controls: dict[str, Resource | None],
        expired: bool = False,
    ) -> SemanticVerdict:
        if (
            parent is None
            or any(
                parent.get("metadata", {}).get(key) != self.accepted["metadata"][key]
                for key in ("uid", "generation")
            )
            or parent.get("spec") != self.accepted.get("spec")
        ):
            return SemanticVerdict("violation", ("accepted_input",))
        if any(
            not self._same_resource(expected, controls.get(name))
            for name, expected in self.controls.items()
        ):
            return SemanticVerdict("violation", ("prune_policy",))
        status = parent.get("status", {})
        ready: Resource = next(
            (c for c in status.get("conditions", []) if c.get("type") == "Ready"), {}
        )
        current = (
            ready.get("observedGeneration") == self.accepted["metadata"]["generation"]
            and status.get("lastAttemptedRevision") == self.revision
        )
        tracked = any(
            e.get("id") == self.inventory_id and e.get("v") == "v1"
            for e in status.get("inventory", {}).get("entries", [])
        )
        if phase == "blocked" and current and ready.get("reason") == "PruneFailed":
            if not self._same_resource(self.victim, victim):
                return SemanticVerdict("violation", ("rejected_delete",))
            if not tracked:
                return SemanticVerdict("violation", ("prune_inventory",))
            if ready.get("status") == "False":
                return SemanticVerdict("satisfied")
        elif (
            phase == "recovered"
            and current
            and ready.get("status") == "True"
            and status.get("lastAppliedRevision") == self.revision
            and victim is None
            and not tracked
        ):
            return SemanticVerdict("satisfied")
        return SemanticVerdict(
            "violation" if expired else "pending", ("prune_unconfirmed",)
        )
