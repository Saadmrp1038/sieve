import json
import os
from collections.abc import Sequence
from pathlib import Path

from sieve_tool.analysis.event_graph import (
    EventGraph,
    EventVertex,
    WriteEvent,
)
from sieve_tool.analysis.trace import assemble_trace
from sieve_tool.common.common import (
    TestContext,
    rmtree_if_exists,
    sieve_built_in_test_patterns,
)
from sieve_tool.events import (
    HEAR_READ_FILTER_FLAG,
    ControllerHear,
    ControllerNonK8sWrite,
    ControllerWrite,
    Mask,
    consistent_event_type,
)
from sieve_tool.faults.intermediate_state import intermediate_state_analysis
from sieve_tool.faults.stale_state import stale_state_analysis
from sieve_tool.faults.unobserved_state import unobserved_state_analysis
from sieve_tool.trace import read_trace

type HearWritePair = tuple[EventVertex[ControllerHear], EventVertex[WriteEvent]]
type WriteHearPair = tuple[EventVertex[ControllerWrite], EventVertex[ControllerHear]]


def base_pass(
    controller_hear_vertices: Sequence[EventVertex[ControllerHear]],
    controller_write_vertices: Sequence[EventVertex[ControllerWrite]],
    controller_non_k8s_write_vertices: Sequence[EventVertex[ControllerNonK8sWrite]],
) -> list[HearWritePair]:
    print("Running base pass...")
    vertex_pairs = []
    write_vertices: list[EventVertex[WriteEvent]] = [
        *controller_write_vertices,
        *controller_non_k8s_write_vertices,
    ]
    for controller_write_vertex in write_vertices:
        for controller_hear_vertex in controller_hear_vertices:
            if controller_write_vertex.content.reconcile_id == -1:
                continue
            # controller_hears can lead to that controller_write
            hear_within_reconcile_scope = (
                controller_write_vertex.content.range_start_timestamp
                < controller_hear_vertex.content.end_timestamp
            )
            write_after_hear = (
                controller_write_vertex.content.start_timestamp
                > controller_hear_vertex.content.start_timestamp
            )
            if hear_within_reconcile_scope and write_after_hear:
                vertex_pairs.append((controller_hear_vertex, controller_write_vertex))
    return vertex_pairs


def hear_read_overlap_filtering_pass(
    vertex_pairs: Sequence[HearWritePair],
) -> list[HearWritePair]:
    print("Running optional pass: hear-read-overlap-filtering...")
    pruned_vertex_pairs = []
    for pair in vertex_pairs:
        controller_hear_vertex = pair[0]
        controller_write_vertex = pair[1]
        write = controller_write_vertex.content
        if isinstance(write, ControllerWrite):
            key_match = controller_hear_vertex.content.key in write.read_keys
            type_match = controller_hear_vertex.content.rtype in write.read_types
            if key_match or type_match:
                pruned_vertex_pairs.append(pair)
        else:
            pruned_vertex_pairs.append(pair)
    print("<e, s> pairs: {} -> {}".format(len(vertex_pairs), len(pruned_vertex_pairs)))
    return pruned_vertex_pairs


def generate_hear_write_pairs(event_graph: EventGraph) -> list[HearWritePair]:
    controller_hear_vertices = event_graph.controller_hear_vertices
    controller_write_vertices = event_graph.controller_write_vertices
    controller_non_k8s_write_vertices = event_graph.controller_non_k8s_write_vertices
    vertex_pairs = base_pass(
        controller_hear_vertices,
        controller_write_vertices,
        controller_non_k8s_write_vertices,
    )
    if HEAR_READ_FILTER_FLAG:
        vertex_pairs = hear_read_overlap_filtering_pass(vertex_pairs)
    return vertex_pairs


def generate_write_hear_pairs(event_graph: EventGraph) -> list[WriteHearPair]:
    vertex_pairs = []
    controller_hear_vertices = event_graph.controller_hear_vertices
    controller_write_vertices = event_graph.controller_write_vertices
    controller_hear_key_map = event_graph.controller_hear_key_to_vertices
    for controller_write_vertex in controller_write_vertices:
        if controller_write_vertex.content.key in controller_hear_key_map:
            for controller_hear_vertex in controller_hear_vertices:
                if (
                    controller_hear_vertex.content.obj_str
                    == controller_write_vertex.content.obj_str
                    and controller_write_vertex.content.start_timestamp
                    < controller_hear_vertex.content.start_timestamp
                    and consistent_event_type(
                        controller_hear_vertex.content.etype,
                        controller_write_vertex.content.etype,
                    )
                ):
                    vertex_pairs.append(
                        (controller_write_vertex, controller_hear_vertex)
                    )
                    break
    return vertex_pairs


def build_controller_event_graph(
    test_context: TestContext, log_path: str | Path, oracle_dir: str | Path
) -> EventGraph:
    with (Path(oracle_dir) / "mask.json").open() as stream:
        learned_masked_paths: Mask = json.load(stream)

    projection = assemble_trace(
        read_trace(log_path),
        compress_trivial=test_context.common_config.compress_trivial_reconcile_enabled,
        loosen_boundary=test_context.controller_config.loosen_reconciler_boundary,
    )

    event_graph = EventGraph(
        learned_masked_paths,
        test_context.common_config.field_key_mask,
        test_context.common_config.field_path_mask,
    )
    event_graph.add_sorted_controller_hears(projection.hears)
    event_graph.add_sorted_reconciler_events(projection.reconciles)

    hear_write_pairs = generate_hear_write_pairs(event_graph)

    for pair in hear_write_pairs:
        event_graph.connect_hear_to_write(pair[0], pair[1])

    event_graph.finalize()
    event_graph.sanity_check()

    return event_graph


def generate_test_plans(
    test_context: TestContext, analysis_mode: str, event_graph: EventGraph
) -> tuple[int, int, int, int]:
    log_dir = test_context.result_dir
    generated_config_dir = os.path.join(log_dir, analysis_mode)
    rmtree_if_exists(generated_config_dir)
    os.makedirs(generated_config_dir)
    if analysis_mode == sieve_built_in_test_patterns.STALE_STATE:
        return stale_state_analysis(event_graph, generated_config_dir, test_context)
    elif analysis_mode == sieve_built_in_test_patterns.UNOBSERVED_STATE:
        return unobserved_state_analysis(
            event_graph, generated_config_dir, test_context
        )
    elif analysis_mode == sieve_built_in_test_patterns.INTERMEDIATE_STATE:
        return intermediate_state_analysis(
            event_graph, generated_config_dir, test_context
        )

    raise ValueError(f"unknown analysis mode: {analysis_mode}")


def generate_test_plans_from_learn_run(
    test_context: TestContext,
) -> None:
    log_dir = test_context.result_dir
    oracle_dir = test_context.oracle_dir

    log_path = os.path.join(log_dir, "sieve-server.log")

    assert os.path.exists(os.path.join(oracle_dir, "mask.json")), (
        "cannot find mask.json"
    )

    event_graph = build_controller_event_graph(test_context, log_path, oracle_dir)
    sieve_learn_result: dict[str, str | dict[str, int]] = {
        "controller": test_context.controller,
        "test": test_context.test_workload,
    }
    for analysis_mode in [
        sieve_built_in_test_patterns.STALE_STATE,
        sieve_built_in_test_patterns.UNOBSERVED_STATE,
        sieve_built_in_test_patterns.INTERMEDIATE_STATE,
    ]:
        (
            baseline_spec_number,
            after_p1_spec_number,
            after_p2_spec_number,
            final_spec_number,
        ) = generate_test_plans(test_context, analysis_mode, event_graph)
        sieve_learn_result[analysis_mode] = {
            "baseline": baseline_spec_number,
            "after_p1": after_p1_spec_number,
            "after_p2": after_p2_spec_number,
            "final": final_spec_number,
        }

    result_filename = "{}/{}-{}.json".format(
        test_context.result_root_dir,
        test_context.controller,
        test_context.test_workload,
    )
    with open(result_filename, "w") as test_result_json:
        json.dump(
            sieve_learn_result,
            test_result_json,
            indent=4,
        )
