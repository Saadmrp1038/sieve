"""Pure semantic verdicts shared by sequence workloads for different operators."""

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from sieve_tool.testing.child_repair import Resource, owned_by


@dataclass(frozen=True)
class SemanticVerdict:
    status: Literal["satisfied", "pending", "violation"]
    reasons: tuple[str, ...] = ()


class LatestStateMonitor:
    def __init__(self, *, accepted: Resource, expected_value: str) -> None:
        self.accepted = deepcopy(accepted)
        self.expected_value = expected_value

    def observe(
        self,
        *,
        parent: Resource | None,
        child: Resource | None,
        effective_value: str | None,
        ready_generation: int | None,
    ) -> SemanticVerdict:
        if (
            parent is None
            or parent.get("metadata", {}).get("uid") != self.accepted["metadata"]["uid"]
        ):
            return SemanticVerdict("violation", ("parent_identity",))
        if (
            parent["metadata"].get("generation")
            != self.accepted["metadata"]["generation"]
            or parent.get("spec") != self.accepted["spec"]
        ):
            return SemanticVerdict("violation", ("accepted_input",))
        if child is not None and not owned_by(child, self.accepted):
            return SemanticVerdict("violation", ("child_ownership",))
        if (
            child is None
            or type(ready_generation) is not int
            or ready_generation != self.accepted["metadata"]["generation"]
        ):
            return SemanticVerdict("pending", ("latest_state_unconfirmed",))
        if effective_value != self.expected_value:
            return SemanticVerdict("violation", ("ready_value",))
        return SemanticVerdict("satisfied")


class MetadataCleanupMonitor:
    """Check only declared metadata obligations, allowing asynchronous cleanup.

    Ready is not a metadata synchronization barrier in either adapter. A mismatch
    is pending until the caller's bounded convergence deadline expires.
    """

    def __init__(
        self,
        *,
        expected: dict[str, dict[str, str]],
        removed: dict[str, tuple[str, ...]],
    ) -> None:
        if any(
            key in expected.get(channel, {})
            for channel, keys in removed.items()
            for key in keys
        ):
            raise ValueError("conflicting metadata presence/absence obligations")
        self.expected, self.removed = deepcopy(expected), deepcopy(removed)

    def observe(
        self, *, child: Resource | None, expired: bool = False
    ) -> SemanticVerdict:
        reasons = []
        metadata = child.get("metadata", {}) if child is not None else {}
        if child is None or any(
            metadata.get(channel, {}).get(key) != value
            for channel, values in self.expected.items()
            for key, value in values.items()
        ):
            reasons.append("metadata_value")
        if any(
            key in metadata.get(channel, {})
            for channel, keys in self.removed.items()
            for key in keys
        ):
            reasons.append("metadata_cleanup")
        return (
            SemanticVerdict("violation" if expired else "pending", tuple(reasons))
            if reasons
            else SemanticVerdict("satisfied")
        )


class HistoryEquivalenceMonitor:
    """Compare final semantics with a healthy fresh-state reference.

    Callers establish input identity/readiness separately. Values are semantic
    adapter projections (for example a validated hostname), never TLS bytes.
    Only explicitly managed metadata keys participate in the comparison.
    """

    def __init__(
        self,
        *,
        reference_parent: Resource,
        reference_child: Resource,
        reference_value: str,
        metadata_keys: dict[str, tuple[str, ...]],
    ) -> None:
        if not owned_by(reference_child, reference_parent):
            raise ValueError("history reference must have verified ownership")
        self.metadata_keys = deepcopy(metadata_keys)
        self.reference = self._project(reference_child, reference_value)

    def _project(self, child: Resource, value: str) -> Resource:
        metadata = child.get("metadata", {})
        return {
            "effective_value": value,
            "type": child.get("type", "Opaque"),
            "metadata": {
                channel: {
                    key: metadata.get(channel, {})[key]
                    for key in keys
                    if key in metadata.get(channel, {})
                }
                for channel, keys in self.metadata_keys.items()
            },
        }

    def observe(
        self,
        *,
        parent: Resource,
        child: Resource | None,
        effective_value: str | None,
    ) -> SemanticVerdict:
        if child is not None and not owned_by(child, parent):
            return SemanticVerdict("violation", ("child_ownership",))
        if child is None or effective_value is None:
            return SemanticVerdict("pending", ("history_unconfirmed",))
        if self._project(child, effective_value) != self.reference:
            return SemanticVerdict("violation", ("history_equivalence",))
        return SemanticVerdict("satisfied")


class StabilityWindow:
    def __init__(self, *, seconds: float) -> None:
        if isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError("stability duration must be finite and nonnegative")
        self.interval_ns = int(seconds * 1_000_000_000)
        self.since: int | None = None
        self.epoch: str | None = None

    def observe(
        self, verdict: SemanticVerdict, *, now_ns: int, epoch: str | None = None
    ) -> bool:
        if epoch != self.epoch:
            self.epoch, self.since = epoch, None
        if verdict.status != "satisfied":
            self.since = None
            return False
        if self.since is None:
            self.since = now_ns
        return now_ns - self.since >= self.interval_ns
