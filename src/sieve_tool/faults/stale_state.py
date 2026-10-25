import os
from typing import List, Tuple

from sieve_tool.analysis.event_graph import (
    EventGraph,
    EventVertex,
    event_vertices_connected,
)
from sieve_tool.common.common import TestContext, bcolors, cprint
from sieve_tool.common.event_delta import same_key
from sieve_tool.common.k8s_event import (
    ALLOWED_ERROR_TYPE,
    ControllerHear,
    ControllerHearTypes,
    ControllerWrite,
    ControllerWriteTypes,
    generate_key,
)
from sieve_tool.faults.common import (
    detectable_event_diff,
    nondeterministic_key,
    object_condition,
)
from sieve_tool.plans import (
    APIServerAction,
    FaultPlan,
    ObjectCondition,
    ReconnectControllerAction,
    TimeoutCondition,
    Trigger,
    TriggerDefinition,
    single_trigger,
    write_plan,
)


def stale_state_detectable_pass(
    test_context: TestContext,
    event_pairs: List[Tuple[EventVertex, EventVertex]],
):
    print("Running stale state detectable pass...")
    candidate_pairs = []
    for pair in event_pairs:
        controller_hear = pair[0].content
        controller_write = pair[1].content
        if nondeterministic_key(test_context, controller_hear) or nondeterministic_key(
            test_context, controller_write
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
            candidate_pairs.append(pair)
    print("{} -> {} edges".format(len(event_pairs), len(candidate_pairs)))
    return candidate_pairs


def get_stale_state_baseline(event_graph: EventGraph):
    controller_hear_vertices = event_graph.controller_hear_vertices
    controller_write_vertices = event_graph.controller_write_vertices
    candidate_pairs = []
    for controller_write_vertex in controller_write_vertices:
        for controller_hear_vertex in controller_hear_vertices:
            controller_write = controller_write_vertex.content
            controller_hear = controller_hear_vertex.content
            if not controller_write.etype == ControllerWriteTypes.DELETE:
                continue
            write_after_hear = (
                controller_write.start_timestamp > controller_hear.start_timestamp
            )

            if write_after_hear:
                pair = (controller_hear_vertex, controller_write_vertex)
                candidate_pairs.append(pair)
    return candidate_pairs


def causality_pair_filtering_pass(
    event_pairs: List[Tuple[EventVertex, EventVertex]],
):
    print("Running optional pass: causality-filtering...")
    candidate_pairs = []
    for pair in event_pairs:
        source = pair[0]
        sink = pair[1]
        if event_vertices_connected(source, sink):
            candidate_pairs.append(pair)
    print("{} -> {} edges".format(len(event_pairs), len(candidate_pairs)))
    return candidate_pairs


def reversed_effect_filtering_pass(
    event_pairs: List[Tuple[EventVertex, EventVertex]],
    event_graph: EventGraph,
):
    print("Running optional pass: reversed-effect-filtering...")
    candidate_pairs = []
    hear_key_to_vertices = event_graph.controller_hear_key_to_vertices
    for pair in event_pairs:
        controller_write = pair[1].content
        assert controller_write.etype == ControllerWriteTypes.DELETE
        if controller_write.error not in ALLOWED_ERROR_TYPE:
            continue
        reversed_effect = False
        if controller_write.key in hear_key_to_vertices:
            for controller_hear_vertex in hear_key_to_vertices[controller_write.key]:
                controller_hear = controller_hear_vertex.content
                if controller_hear.start_timestamp <= controller_write.end_timestamp:
                    continue
                if controller_hear.etype == ControllerHearTypes.ADDED:
                    reversed_effect = True
                if controller_hear.etype == ControllerHearTypes.UPDATED:
                    reversed_effect = True
        else:
            # if the controller_write key never appears in the controller_hear_key_map
            # it means the operator does not watch on the resource
            # so we should be cautious and keep this edge
            reversed_effect = True
        if reversed_effect:
            candidate_pairs.append(pair)
    print("{} -> {} edges".format(len(event_pairs), len(candidate_pairs)))
    return candidate_pairs


def decide_stale_state_timing(source_vertex: EventVertex, sink_vertex: EventVertex):
    assert source_vertex.is_controller_hear()
    assert sink_vertex.is_controller_write()
    assert sink_vertex.content.etype == ControllerWriteTypes.DELETE
    if source_vertex.content.slim_prev_obj_map is None:
        return "after"
    if not same_key(
        source_vertex.content.slim_prev_obj_map, source_vertex.content.slim_cur_obj_map
    ):
        return "after"
    reconcile_cnt = 0
    cur_vertex = sink_vertex
    while True:
        if len(cur_vertex.out_intra_reconciler_edges) == 1:
            next_vertex = cur_vertex.out_intra_reconciler_edges[0].sink
            if next_vertex.is_reconcile_end():
                reconcile_cnt += 1
                if reconcile_cnt == 2:
                    return "after"
            elif next_vertex.is_controller_write():
                if (
                    next_vertex.content.etype == ControllerWriteTypes.CREATE
                    and next_vertex.content.key == sink_vertex.content.key
                ):
                    if reconcile_cnt == 0:
                        print("decide timing: before")
                        return "before"
                    else:
                        print("decide timing: both")
                        return "both"
            cur_vertex = next_vertex
        elif len(sink_vertex.out_intra_reconciler_edges) == 0:
            return "after"
        else:
            raise AssertionError()


def generate_stale_state_test_plan(
    test_context: TestContext,
    controller_hear: ControllerHear,
    controller_write: ControllerWrite,
    pause_timing: str,
) -> FaultPlan:
    heard_resource = generate_key(
        controller_hear.rtype, controller_hear.namespace, controller_hear.name
    )
    written_resource = generate_key(
        controller_write.rtype, controller_write.namespace, controller_write.name
    )
    condition = object_condition(
        controller_hear,
        convert_state=controller_hear.rtype
        not in test_context.controller_config.custom_resource_definitions,
    )
    following_api = test_context.common_config.following_api
    return FaultPlan(
        workload=test_context.test_workload,
        actions=[
            APIServerAction(
                action_type="pauseAPIServer",
                api_server_name=following_api,
                pause_scope=heard_resource,
                trigger=single_trigger(
                    "trigger1",
                    condition,
                    "afterAPIServerRecv"
                    if pause_timing == "after"
                    else "beforeAPIServerRecv",
                    following_api,
                ),
            ),
            ReconnectControllerAction(
                action_type="reconnectController",
                controller_label=test_context.controller_config.controller_pod_label,
                reconnect_api_server=following_api,
                async_=True,
                wait_before=10,
                trigger=single_trigger(
                    "trigger2",
                    ObjectCondition(
                        condition_type="onObjectCreate",
                        resource_key=written_resource,
                        occurrence=1,
                    ),
                    "afterAPIServerRecv",
                    test_context.common_config.leading_api,
                ),
            ),
            APIServerAction(
                action_type="resumeAPIServer",
                api_server_name=following_api,
                pause_scope=heard_resource,
                trigger=Trigger(
                    definitions=[
                        TriggerDefinition(
                            trigger_name="trigger3",
                            condition=TimeoutCondition(
                                condition_type="onTimeout", timeout_value=20
                            ),
                        )
                    ],
                    expression="trigger3",
                ),
            ),
        ],
    )


def stale_state_analysis(event_graph: EventGraph, path: str, test_context: TestContext):
    candidate_pairs = get_stale_state_baseline(event_graph)
    baseline_spec_number = len(candidate_pairs)
    after_p1_spec_number = -1
    after_p2_spec_number = -1
    final_spec_number = -1
    if test_context.common_config.causality_pruning_enabled:
        candidate_pairs = causality_pair_filtering_pass(candidate_pairs)
        after_p1_spec_number = len(candidate_pairs)
    if test_context.common_config.effective_updates_pruning_enabled:
        candidate_pairs = reversed_effect_filtering_pass(candidate_pairs, event_graph)
        after_p2_spec_number = len(candidate_pairs)
    if test_context.common_config.nondeterministic_pruning_enabled:
        candidate_pairs = stale_state_detectable_pass(test_context, candidate_pairs)
    final_spec_number = len(candidate_pairs)
    i = 0
    for pair in candidate_pairs:
        source = pair[0]
        sink = pair[1]
        controller_hear = source.content
        controller_write = sink.content
        assert isinstance(controller_hear, ControllerHear)
        assert isinstance(controller_write, ControllerWrite)

        timing = decide_stale_state_timing(source, sink)

        if timing == "after":
            stale_state_test_plan = generate_stale_state_test_plan(
                test_context, controller_hear, controller_write, timing
            )
            i += 1
            file_name = os.path.join(
                path, "stale-state-test-plan-{}.yaml".format(str(i))
            )
            if test_context.common_config.persist_test_plans_enabled:
                write_plan(stale_state_test_plan, file_name)
        elif timing == "before":
            stale_state_test_plan = generate_stale_state_test_plan(
                test_context, controller_hear, controller_write, timing
            )
            i += 1
            file_name = os.path.join(
                path, "stale-state-test-plan-{}.yaml".format(str(i))
            )
            if test_context.common_config.persist_test_plans_enabled:
                write_plan(stale_state_test_plan, file_name)
        else:
            stale_state_test_plan = generate_stale_state_test_plan(
                test_context, controller_hear, controller_write, "after"
            )
            i += 1
            file_name = os.path.join(
                path, "stale-state-test-plan-{}.yaml".format(str(i))
            )
            if test_context.common_config.persist_test_plans_enabled:
                write_plan(stale_state_test_plan, file_name)

            stale_state_test_plan = generate_stale_state_test_plan(
                test_context, controller_hear, controller_write, "before"
            )
            i += 1
            file_name = os.path.join(
                path, "stale-state-test-plan-{}.yaml".format(str(i))
            )
            if test_context.common_config.persist_test_plans_enabled:
                write_plan(stale_state_test_plan, file_name)
            baseline_spec_number += 1
            after_p1_spec_number += 1
            after_p2_spec_number += 1
            final_spec_number += 1

    cprint(
        "Generated {} stale-state test plan(s) in {}".format(i, path), bcolors.OKGREEN
    )
    return (
        baseline_spec_number,
        after_p1_spec_number,
        after_p2_spec_number,
        final_spec_number,
    )
