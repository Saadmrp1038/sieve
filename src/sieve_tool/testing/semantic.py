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
