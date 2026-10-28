import os
from collections import Counter
from collections.abc import Sequence

from sieve_tool.analysis.event_graph import (
    EventGraph,
    EventVertex,
)
from sieve_tool.common.common import TestContext, bcolors, cprint
from sieve_tool.common.event_delta import diff_event
from sieve_tool.events import (
    ALLOWED_ERROR_TYPE,
    ControllerNonK8sWrite,
    ControllerWrite,
    ControllerWriteTypes,
    is_creation_or_deletion,
)
from sieve_tool.faults.common import (
    detectable_event_diff,
    nondeterministic_key,
    object_condition,
)
from sieve_tool.plans import (
    APICallCondition,
    FaultPlan,
    ObjectCondition,
    RestartControllerAction,
    UpdateCondition,
    single_trigger,
    write_plan,
)


def intermediate_state_detectable_pass(
    test_context: TestContext, event_vertices: Sequence[EventVertex[ControllerWrite]]
) -> list[EventVertex[ControllerWrite]]:
    print("Running intermediate state detectable pass...")
    candidate_vertices = []
    for vertex in event_vertices:
        assert vertex.is_controller_write()
        controller_write = vertex.content
        if nondeterministic_key(
            test_context,
            controller_write,
        ):
            continue
        if detectable_event_diff(
            False,
            controller_write.slim_prev_obj_map,
            controller_write.slim_cur_obj_map,
            controller_write.prev_etype,
            controller_write.etype,
            controller_write.signature_counter,
        ):
            candidate_vertices.append(vertex)
    print("{} -> {} writes".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def effective_write_filtering_pass(
    event_vertices: Sequence[EventVertex[ControllerWrite]],
) -> list[EventVertex[ControllerWrite]]:
    print("Running optional pass:  effective-write-filtering...")
    candidate_vertices = []
    for vertex in event_vertices:
        assert vertex.is_controller_write()
        if is_creation_or_deletion(vertex.content.etype):
            candidate_vertices.append(vertex)
        else:
            assert vertex.content.prev_obj_map is not None
            assert vertex.content.obj_map is not None
            unmasked_prev_object, unmasked_cur_object = diff_event(
                vertex.content.prev_obj_map,
                vertex.content.obj_map,
                set(),
                set(),
                True,
                False,
            )
            cur_etype = vertex.content.etype
            empty_write = False
            if unmasked_prev_object == unmasked_cur_object and (
                cur_etype == ControllerWriteTypes.UPDATE
                or cur_etype == ControllerWriteTypes.PATCH
                or cur_etype == ControllerWriteTypes.STATUS_UPDATE
                or cur_etype == ControllerWriteTypes.STATUS_PATCH
            ):
                empty_write = True
            elif (
                unmasked_prev_object is not None
                and "status" not in unmasked_prev_object
                and unmasked_cur_object is not None
                and "status" not in unmasked_cur_object
                and (
                    cur_etype == ControllerWriteTypes.STATUS_UPDATE
                    or cur_etype == ControllerWriteTypes.STATUS_PATCH
                )
            ):
                empty_write = True
            if not empty_write:
                candidate_vertices.append(vertex)
    print("{} -> {} writes".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def no_error_write_filtering_pass(
    event_vertices: Sequence[EventVertex[ControllerWrite]],
) -> list[EventVertex[ControllerWrite]]:
    print("Running optional pass:  no-error-write-filtering...")
    candidate_vertices = []
    for vertex in event_vertices:
        assert vertex.is_controller_write()
        if vertex.content.error in ALLOWED_ERROR_TYPE:
            candidate_vertices.append(vertex)
    print("{} -> {} writes".format(len(event_vertices), len(candidate_vertices)))
    return candidate_vertices


def generate_intermediate_state_test_plan_for_controller_write(
    test_context: TestContext,
    controller_write: ControllerWrite,
    condition: ObjectCondition | UpdateCondition | None = None,
) -> FaultPlan:
    return FaultPlan(
        workload=test_context.test_workload,
        actions=[
            RestartControllerAction(
                action_type="restartController",
                controller_label=test_context.controller_config.controller_pod_label,
                trigger=single_trigger(
                    "trigger1",
                    condition
                    if condition is not None
                    else object_condition(controller_write),
                    "afterControllerWrite",
                    controller_write.reconcile_fun,
                ),
            )
        ],
    )


def generate_intermediate_state_test_plan_for_annotated_api_invocation(
    test_context: TestContext,
    api_invocation: ControllerNonK8sWrite,
) -> FaultPlan:
    condition = APICallCondition(
        condition_type="onAnnotatedAPICall",
        module=api_invocation.module,
        file_path=api_invocation.file_path,
        receiver_type=api_invocation.recv_type,
        fun_name=api_invocation.fun_name,
        occurrence=api_invocation.signature_counter,
    )
    return FaultPlan(
        workload=test_context.test_workload,
        actions=[
            RestartControllerAction(
                action_type="restartController",
                controller_label=test_context.controller_config.controller_pod_label,
                trigger=single_trigger(
                    "trigger1",
                    condition,
                    "afterAnnotatedAPICall",
                    api_invocation.reconcile_fun,
                ),
            )
        ],
    )


def intermediate_state_analysis(
    event_graph: EventGraph, path: str, test_context: TestContext
) -> tuple[int, int, int, int]:
    settings = getattr(test_context.controller_config, "test_setting", {}).get(
        test_context.test_workload, {}
    )
    operation_conditions: dict[int, ObjectCondition | UpdateCondition] = {}
    if settings.get("write_trigger") == "occurrence":
        # Runtime forwards successful writes only and merges all update verbs.
        # Count before pruning, by the exact selector and reconciler used there.
        counts: Counter[tuple[str, str, str]] = Counter()
        for vertex in event_graph.controller_write_vertices:
            event = vertex.content
            if event.error != "NoError":
                continue
            patterns = [
                p
                for p in settings.get("generated_name_prefixes", [])
                if event.key.startswith(p[:-1])
            ]
            if len(patterns) > 1:
                raise ValueError("overlapping generated_name_prefixes")
            key = patterns[0] if patterns else event.key
            kind = {"Create": "onObjectCreate", "Delete": "onObjectDelete"}.get(
                event.etype, "onObjectUpdate"
            )
            counter_key = (event.reconcile_fun, key, kind)
            counts[counter_key] += 1
            values = dict(resource_key=key, occurrence=counts[counter_key])
            if patterns:
                values["resource_key_pattern"] = True
            operation_conditions[event.id] = (
                ObjectCondition.model_validate(dict(condition_type=kind, **values))
                if kind != "onObjectUpdate"
                else UpdateCondition.model_validate(dict(condition_type=kind, **values))
            )
    candidate_write_vertices = event_graph.controller_write_vertices
    candidate_annotated_api_invocation_vertices = (
        event_graph.controller_non_k8s_write_vertices
    )
    baseline_spec_number = len(candidate_write_vertices) + len(
        candidate_annotated_api_invocation_vertices
    )
    after_p1_spec_number = -1
    after_p2_spec_number = -1
    final_spec_number = -1
    after_p1_spec_number = len(candidate_write_vertices) + len(
        candidate_annotated_api_invocation_vertices
    )
    if test_context.common_config.effective_updates_pruning_enabled:
        candidate_write_vertices = effective_write_filtering_pass(
            candidate_write_vertices
        )
        candidate_write_vertices = no_error_write_filtering_pass(
            candidate_write_vertices
        )
        after_p2_spec_number = len(candidate_write_vertices) + len(
            candidate_annotated_api_invocation_vertices
        )
    if test_context.common_config.nondeterministic_pruning_enabled:
        candidate_write_vertices = intermediate_state_detectable_pass(
            test_context, candidate_write_vertices
        )
    final_spec_number = len(candidate_write_vertices) + len(
        candidate_annotated_api_invocation_vertices
    )
    i = 0
    for vertex in candidate_write_vertices:
        controller_write = vertex.content
        if (
            settings.get("write_trigger") == "occurrence"
            and controller_write.id not in operation_conditions
        ):
            continue
        intermediate_state_test_plan = (
            generate_intermediate_state_test_plan_for_controller_write(
                test_context,
                controller_write,
                operation_conditions.get(controller_write.id),
            )
        )
        i += 1
        file_name = os.path.join(
            path, "intermediate-state-test-plan-{}.yaml".format(str(i))
        )
        if test_context.common_config.persist_test_plans_enabled:
            write_plan(intermediate_state_test_plan, file_name)

    for annotated_vertex in candidate_annotated_api_invocation_vertices:
        annotated_api_invocation = annotated_vertex.content
        intermediate_state_test_plan = (
            generate_intermediate_state_test_plan_for_annotated_api_invocation(
                test_context, annotated_api_invocation
            )
        )
        i += 1
        file_name = os.path.join(
            path, "intermediate-state-test-plan-{}.yaml".format(str(i))
        )
        if test_context.common_config.persist_test_plans_enabled:
            write_plan(intermediate_state_test_plan, file_name)
    cprint(
        "Generated {} intermediate-state test plan(s) in {}".format(i, path),
        bcolors.OKGREEN,
    )
    return (
        baseline_spec_number,
        after_p1_spec_number,
        after_p2_spec_number,
        final_spec_number,
    )
