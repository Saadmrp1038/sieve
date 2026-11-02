"""Generate short operation histories, including returning to prior states."""

import json
from dataclasses import dataclass
from typing import Literal

from sieve_tool.plans import (
    FaultPlan,
    RestartControllerAction,
    StateMatchCondition,
    single_trigger,
)

PHASE_LABEL = "sieve.testing/state"
HISTORY_KEY = "sieve.testing/history"
KEEP_KEY = "sieve.testing/keep"


@dataclass(frozen=True)
class MetadataStep:
    operation: Literal["add", "change", "remove"]
    value: str | None


@dataclass(frozen=True)
class MetadataHistory:
    channel: Literal["labels", "annotations"]
    steps: tuple[MetadataStep, ...]


def metadata_histories(*, max_histories: int = 8) -> list[MetadataHistory]:
    """Legal, bounded entry lifetimes; unrelated entries remain in both maps."""
    if type(max_histories) is not int or not 1 <= max_histories <= 8:
        raise ValueError("metadata history limit requires 1–8 histories")
    add = MetadataStep("add", "one")
    change = MetadataStep("change", "two")
    remove = MetadataStep("remove", None)
    channels: tuple[Literal["labels", "annotations"], ...] = ("labels", "annotations")
    return [
        MetadataHistory(channel, steps)
        for channel in channels
        for steps in ((add,), (add, change), (add, remove), (add, change, remove))
    ][:max_histories]


def bounded_sequences(
    *, initial: str, states: tuple[str, ...], max_steps: int, max_sequences: int
) -> list[tuple[str, ...]]:
    if (
        type(max_steps) is not int
        or not 1 <= max_steps <= 3
        or type(max_sequences) is not int
        or not 1 <= max_sequences <= 16
    ):
        raise ValueError("sequence bounds require 1–3 steps and 1–16 sequences")
    if (
        not 2 <= len(states) <= 4
        or any(not isinstance(state, str) or not state.strip() for state in states)
        or len(set(states)) != len(states)
        or initial not in states
    ):
        raise ValueError("states must contain 2–4 distinct values including initial")
    result: list[tuple[str, ...]] = []
    frontier: list[tuple[str, ...]] = [()]
    for _ in range(max_steps):
        following = []
        for prefix in frontier:
            previous = prefix[-1] if prefix else initial
            for state in states:
                if state == previous:
                    continue
                path = (*prefix, state)
                result.append(path)
                if len(result) == max_sequences:
                    return result
                following.append(path)
        frontier = following
    return result


def sequence_plans(
    *, controller_label: str, parent_key: str, selected: tuple[str, ...]
) -> dict[str, FaultPlan]:
    """Produce a matched control and one asynchronous restart at the first received update."""
    return {
        "sequence-no-fault.yaml": FaultPlan(workload="bounded-sequences", actions=[]),
        "sequence-restart.yaml": FaultPlan(
            workload="bounded-sequences",
            actions=[
                RestartControllerAction(
                    action_type="restartController",
                    controller_label=controller_label,
                    async_=True,
                    trigger=single_trigger(
                        "first_sequence_update",
                        StateMatchCondition(
                            condition_type="onObjectUpdateTo",
                            resource_key=parent_key,
                            occurrence=1,
                            state_match=json.dumps(
                                {
                                    "metadata": {
                                        "labels": {
                                            PHASE_LABEL: "sequence-1-" + selected[0]
                                        }
                                    }
                                }
                            ),
                        ),
                        "afterControllerRecv",
                        "informer",
                    ),
                )
            ],
        ),
    }
