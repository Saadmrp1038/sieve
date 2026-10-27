"""Generate missed-state schedules backed by independently produced overwrites."""

import hashlib
import json
from pathlib import Path

from sieve_tool.common.common import TestContext
from sieve_tool.events import ControllerHear, ControllerRead
from sieve_tool.plans import (
    FaultPlan,
    ObservationPoint,
    PauseControllerAction,
    ResumeControllerAction,
    StateMatchCondition,
    Trigger,
    TriggerDefinition,
    single_trigger,
    write_plan,
)
from sieve_tool.testing.updates import WorkloadUpdate, contains_state, read_updates
from sieve_tool.trace import Marker, read_trace


def independent_update_analysis(
    context: TestContext, output: str
) -> tuple[int, int, int, int]:
    directory = Path(context.result_dir)
    source_file = directory / "workload-updates.json"
    source = read_updates(source_file)
    if source.workload != context.test_workload:
        raise ValueError("independent updates belong to a different workload")
    records = read_trace(directory / "sieve-server.log").records
    hears = [
        r.event
        for r in records
        if isinstance(r.event, ControllerHear) and r.event.key == source.resource_key
    ]
    reads = [
        r.event
        for r in records
        if r.marker == Marker.CACHE_READ
        and isinstance(r.event, ControllerRead)
        and r.event.etype == "Get"
        and r.event.error == "NoError"
        and source.resource_key in r.event.key_to_obj
    ]
    annotated = set(context.controller_config.annotated_reconcile_functions.values())
    consumers = sorted({r.reconcile_fun for r in reads if r.reconcile_fun in annotated})
    observers = sorted({h.observed_by for h in hears})
    evidence: dict[str, object] = {
        "mode": "independent-workload-updates",
        "source_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest(),
        "resource_key": source.resource_key,
        "plans": [],
        "rejected": [],
    }
    planned: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    seen_windows = 0

    def received(event: ControllerHear, update: WorkloadUpdate) -> bool:
        metadata = event.obj_map.get("metadata", {})
        accepted = update.response["metadata"]
        return (
            metadata.get("uid") == accepted["uid"]
            and metadata.get("resourceVersion") == accepted["resourceVersion"]
            and contains_state(event.obj_map, update.state)
        )

    for index in range(1, len(source.updates) - 1):
        intermediate, later = source.updates[index : index + 2]
        if not observers or any(
            not any(h.observed_by == observer and received(h, update) for h in hears)
            for observer in observers
            for update in (intermediate, later)
        ):
            rejected.append(
                {
                    "update_index": index,
                    "reason": "external update missing from an informer stream",
                }
            )
            continue
        seen_windows += len(consumers)
        for consumer in consumers:
            if not any(
                r.reconcile_fun == consumer
                and contains_state(
                    r.key_to_obj[source.resource_key], intermediate.state
                )
                for r in reads
            ):
                rejected.append(
                    {
                        "update_index": index,
                        "consumer": consumer,
                        "reason": "baseline did not read the intermediate state",
                    }
                )
                continue
            definitions = [
                TriggerDefinition(
                    trigger_name=f"cache{i}",
                    condition=StateMatchCondition(
                        condition_type="onObjectUpdateTo",
                        resource_key=source.resource_key,
                        occurrence=1,
                        state_match=json.dumps(later.state, sort_keys=True),
                    ),
                    observation_point=ObservationPoint(
                        when="afterControllerRecv", by=observer
                    ),
                )
                for i, observer in enumerate(observers)
            ]
            plan = FaultPlan(
                workload=context.test_workload,
                actions=[
                    PauseControllerAction(
                        action_type="pauseController",
                        pause_at="beforeControllerRead",
                        pause_scope=source.resource_key,
                        pause_by=consumer,
                        avoid_ongoing_read=True,
                        trigger=single_trigger(
                            "intermediate",
                            StateMatchCondition(
                                condition_type="onObjectUpdateTo",
                                resource_key=source.resource_key,
                                occurrence=1,
                                state_match=json.dumps(
                                    intermediate.state, sort_keys=True
                                ),
                                convert_state_to_api_form=source.resource_key.split(
                                    "/", 1
                                )[0]
                                not in context.controller_config.custom_resource_definitions,
                            ),
                            "beforeAPIServerRecv",
                            context.common_config.leading_api,
                        ),
                    ),
                    ResumeControllerAction(
                        action_type="resumeController",
                        pause_at="beforeControllerRead",
                        pause_scope=source.resource_key,
                        trigger=Trigger(
                            definitions=definitions,
                            expression="&".join(d.trigger_name for d in definitions),
                        ),
                    ),
                ],
            )
            filename = f"unobserved-state-test-plan-{len(planned) + 1}.yaml"
            if context.common_config.persist_test_plans_enabled:
                write_plan(plan, Path(output) / filename)
            planned.append(
                {
                    "file": filename,
                    "update_index": index,
                    "later_index": index + 1,
                    "paused_by": consumer,
                    "observers": observers,
                    "intermediate_state": intermediate.state,
                    "later_state": later.state,
                }
            )
    evidence.update(plans=planned, rejected=rejected)
    (directory / "unobserved-state-evidence.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    count = len(planned)
    return (len(source.updates) - 2) * len(consumers), seen_windows, count, count
