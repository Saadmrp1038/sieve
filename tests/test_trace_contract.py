"""Fixed parser, interval, graph and plan expectations from the previous engine."""

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sieve_tool.analysis.analyze import (
    build_controller_event_graph,
    generate_test_plans,
    parse_receiver_events,
    parse_reconciler_events,
    sanity_check_sieve_server_log,
)
from sieve_tool.common import k8s_event as events
from sieve_tool.common.config import get_common_config, load_controller_config

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    "id",
    "etype",
    "rtype",
    "namespace",
    "name",
    "key",
    "obj_str",
    "obj_map",
    "reconcile_fun",
    "reconcile_id",
    "error",
    "from_cache",
    "key_set",
    "key_to_obj",
    "start_timestamp",
    "end_timestamp",
    "range_start_timestamp",
    "range_end_timestamp",
    "read_types",
    "read_keys",
    "prev_obj_map",
    "slim_prev_obj_map",
    "slim_cur_obj_map",
    "prev_etype",
    "signature_counter",
    "cancelled_by",
    "module",
    "file_path",
    "recv_type",
    "fun_name",
)
DECODERS = {
    events.SIEVE_BEFORE_HEAR_MARK: events.parse_controller_hear,
    events.SIEVE_AFTER_HEAR_MARK: events.parse_controller_hear_id_only,
    events.SIEVE_BEFORE_REST_WRITE_MARK: events.parse_controller_write_id_only,
    events.SIEVE_AFTER_REST_WRITE_MARK: events.parse_controller_write,
    events.SIEVE_AFTER_REST_READ_MARK: events.parse_controller_read,
    events.SIEVE_AFTER_CACHE_READ_MARK: events.parse_controller_cache_read,
    events.SIEVE_BEFORE_RECONCILE_MARK: events.parse_reconcile,
    events.SIEVE_AFTER_RECONCILE_MARK: events.parse_reconcile,
    events.SIEVE_BEFORE_ANNOTATED_API_INVOCATION_MARK: events.parse_controller_non_k8s_write_id_only,
    events.SIEVE_AFTER_ANNOTATED_API_INVOCATION_MARK: events.parse_controller_non_k8s_write,
    events.SIEVE_API_EVENT_MARK: events.parse_api_event,
}


def normalize(value):
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


def event_snapshot(event):
    return {
        name: normalize(getattr(event, name)) for name in FIELDS if hasattr(event, name)
    }


def snapshot(case, output, compress=True, loosen=False):
    trace = output / "input.log"
    trace.write_text(gzip.decompress((case / "trace.log.gz").read_bytes()).decode())
    name = (
        "rabbitmq-operator"
        if case.name == "rabbitmq"
        else "kubernetes-secret-generator"
    )
    common = get_common_config()
    common.compress_trivial_reconcile_enabled = compress
    controller = load_controller_config(ROOT / "controllers" / name)
    controller.loosen_reconciler_boundary = loosen
    context = SimpleNamespace(
        result_dir=str(output),
        oracle_dir=str(case),
        common_config=common,
        controller_config=controller,
        test_workload="fixture",
    )
    records = []
    for index, line in enumerate(trace.read_text().splitlines(keepends=True)):
        for marker, decoder in DECODERS.items():
            if marker in line:
                records.append(
                    {
                        "position": index,
                        "marker": marker,
                        "event": event_snapshot(decoder(line)),
                    }
                )
                break
    sanity_check_sieve_server_log(trace)
    receipts = parse_receiver_events(trace)
    reconciles = parse_reconciler_events(context, trace)
    graph = build_controller_event_graph(context, trace, case)
    groups = {
        kind: getattr(graph, kind + "_vertices")
        for kind in [
            "controller_hear",
            "controller_write",
            "controller_non_k8s_write",
            "controller_read",
            "reconcile_begin",
            "reconcile_end",
        ]
    }
    vertices = {
        kind: [
            {"gid": vertex.gid, "event": event_snapshot(vertex.content)}
            for vertex in values
        ]
        for kind, values in groups.items()
    }
    edges = {
        kind: [
            [edge.source.gid, edge.sink.gid, edge.type] for edge in getattr(graph, kind)
        ]
        for kind in [
            "controller_hear_controller_write_edges",
            "controller_write_controller_hear_edges",
            "intra_reconciler_edges",
        ]
    }
    plans = {}
    for family in ["stale-state", "unobserved-state", "intermediate-state"]:
        counts = generate_test_plans(context, family, graph)
        plans[family] = {
            "counts": list(counts),
            "plans": [
                yaml.safe_load(path.read_text())
                for path in sorted((output / family).glob("*.yaml"))
            ],
        }
    return {
        "records": records,
        "receipts": [event_snapshot(e) for e in receipts],
        "reconciles": [event_snapshot(e) for e in reconciles],
        "vertices": vertices,
        "edges": edges,
        "plans": plans,
    }


@pytest.mark.parametrize(
    "name", ["secret-generator", "rabbitmq", "interleaved", "annotated"]
)
@pytest.mark.parametrize(
    "compress,loosen", [(True, False), (False, False), (True, True)]
)
def test_trace_interpretation_matches_the_original_engine(
    tmp_path, name, compress, loosen
):
    case = ROOT / "fixtures/traces" / name
    expected = json.loads(
        gzip.decompress(
            (case / f"expected-{int(compress)}-{int(loosen)}.json.gz").read_bytes()
        ).decode()
    )
    assert snapshot(case, tmp_path, compress, loosen) == expected


def test_annotated_writes_inside_reconciliation_are_currently_unsupported():
    case = ROOT / "fixtures/traces/annotated"
    context = SimpleNamespace(
        common_config=get_common_config(),
        controller_config=load_controller_config(
            ROOT / "controllers/kubernetes-secret-generator"
        ),
    )
    with pytest.raises(AttributeError, match="set_range"):
        parse_reconciler_events(context, case / "unsupported-in-reconcile.log")
