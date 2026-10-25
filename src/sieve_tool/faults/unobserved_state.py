import json
import os
from collections.abc import Sequence

from sieve_tool.analysis.event_graph import (
    EventGraph,
    EventVertex,
)
from sieve_tool.common.common import TestContext, bcolors, cprint
from sieve_tool.events import (
    ControllerHear,
    ControllerHearTypes,
    generate_key,
)
from sieve_tool.faults.common import (
    detectable_event_diff,
    nondeterministic_key,
    object_condition,
)
from sieve_tool.plans import (
    FaultPlan,
    ObjectCondition,
    ObservationPoint,
    PauseControllerAction,
    ResumeControllerAction,
    Trigger,
    TriggerDefinition,
    UpdateCondition,
    single_trigger,
    write_plan,
)


def unobserved_state_detectable_pass(
    test_context: TestContext, event_vertices: Sequence[EventVertex[ControllerHear]]
) -> list[EventVertex[ControllerHear]]:
    print("Running unobserved state detectable pass...")
    candidate_vertices = []
    for vertex in event_vertices:
        controller_hear = vertex.content
        if nondeterministic_key(
            test_context,
            controller_hear,
        ):
            continue
        if detectable_event_diff(
            True,
            controller_hear.slim_prev_obj_map,
            controller_hear.slim_cur_obj_map,
            controller_hear.prev_etype,
            controller_hear.etype,
            controller_hear.signature_counter,
        ):
            candidate_vertices.append(vertex)
    print("{} -> {} receipts".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def causality_hear_filtering_pass(
    event_vertices: Sequence[EventVertex[ControllerHear]],
) -> list[EventVertex[ControllerHear]]:
    print("Running optional pass: causality-filtering...")
    candidate_vertices = []
    for vertex in event_vertices:
        if len(vertex.out_inter_reconciler_edges) > 0:
            candidate_vertices.append(vertex)
    print("{} -> {} receipts".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def overwrite_filtering_pass(
    event_vertices: Sequence[EventVertex[ControllerHear]],
) -> list[EventVertex[ControllerHear]]:
    print("Running optional pass: overwrite-filtering...")
    candidate_vertices = []
    for vertex in event_vertices:
        if len(vertex.content.cancelled_by) > 0:
            candidate_vertices.append(vertex)
    print("{} -> {} receipts".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def generate_unobserved_state_test_plan(
    test_context: TestContext,
    controller_hear: ControllerHear,
) -> FaultPlan:
    resource = generate_key(
        controller_hear.rtype, controller_hear.namespace, controller_hear.name
    )
    next_condition: ObjectCondition | UpdateCondition
    final_condition: ObjectCondition | UpdateCondition
    if controller_hear.etype == ControllerHearTypes.ADDED:
        next_condition = UpdateCondition(
            condition_type="onObjectUpdate", resource_key=resource, occurrence=1
        )
        final_condition = ObjectCondition(
            condition_type="onObjectDelete", resource_key=resource, occurrence=1
        )
    elif controller_hear.etype == ControllerHearTypes.DELETED:
        next_condition = ObjectCondition(
            condition_type="onObjectCreate", resource_key=resource, occurrence=1
        )
        final_condition = UpdateCondition(
            condition_type="onObjectUpdate", resource_key=resource, occurrence=1
        )
    else:
        next_condition = UpdateCondition(
            condition_type="onAnyFieldModification",
            resource_key=resource,
            occurrence=1,
            prev_state_diff=json.dumps(
                controller_hear.slim_cur_obj_map, sort_keys=True
            ),
        )
        final_condition = ObjectCondition(
            condition_type="onObjectDelete", resource_key=resource, occurrence=1
        )
    observation = ObservationPoint(when="afterControllerRecv", by="informer")
    return FaultPlan(
        workload=test_context.test_workload,
        actions=[
            PauseControllerAction(
                action_type="pauseController",
                pause_at="beforeControllerRead",
                pause_scope=resource,
                avoid_ongoing_read=True,
                trigger=single_trigger(
                    "trigger1",
                    object_condition(controller_hear),
                    "beforeControllerRecv",
                    "informer",
                ),
            ),
            ResumeControllerAction(
                action_type="resumeController",
                pause_at="beforeControllerRead",
                pause_scope=resource,
                trigger=Trigger(
                    definitions=[
                        TriggerDefinition(
                            trigger_name="trigger2",
                            condition=next_condition,
                            observation_point=observation,
                        ),
                        TriggerDefinition(
                            trigger_name="trigger3",
                            condition=final_condition,
                            observation_point=observation,
                        ),
                    ],
                    expression="trigger2|trigger3",
                ),
            ),
        ],
    )


def unobserved_state_analysis(
    event_graph: EventGraph, path: str, test_context: TestContext
) -> tuple[int, int, int, int]:
    candidate_vertices = event_graph.controller_hear_vertices
    candidate_vertices = overwrite_filtering_pass(candidate_vertices)
    baseline_spec_number = len(candidate_vertices)
    after_p1_spec_number = -1
    after_p2_spec_number = -1
    final_spec_number = -1
    if test_context.common_config.causality_pruning_enabled:
        candidate_vertices = causality_hear_filtering_pass(candidate_vertices)
        after_p1_spec_number = len(candidate_vertices)
        after_p2_spec_number = len(candidate_vertices)
    if test_context.common_config.nondeterministic_pruning_enabled:
        candidate_vertices = unobserved_state_detectable_pass(
            test_context, candidate_vertices
        )
    final_spec_number = len(candidate_vertices)
    i = 0
    for vertex in candidate_vertices:
        controller_hear = vertex.content
        assert isinstance(controller_hear, ControllerHear)

        unobserved_state_test_plan = generate_unobserved_state_test_plan(
            test_context, controller_hear
        )

        i += 1
        file_name = os.path.join(
            path, "unobserved-state-test-plan-{}.yaml".format(str(i))
        )
        if test_context.common_config.persist_test_plans_enabled:
            write_plan(unobserved_state_test_plan, file_name)

    cprint(
        "Generated {} unobserved-state test plan(s) in {}".format(i, path),
        bcolors.OKGREEN,
    )
    return (
        baseline_spec_number,
        after_p1_spec_number,
        after_p2_spec_number,
        final_spec_number,
    )
