"""Generate short operation histories, including returning to prior states."""

import json

from sieve_tool.plans import (
    FaultPlan,
    RestartControllerAction,
    StateMatchCondition,
    single_trigger,
)

PHASE_LABEL = "sieve.testing/state"


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
