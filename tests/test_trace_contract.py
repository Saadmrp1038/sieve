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
)
from sieve_tool.analysis.trace import assemble_trace
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.trace import TraceError, read_trace

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
    parsed = read_trace(trace)
    records = [
        {
            "position": r.position,
            "marker": r.marker.value,
            "event": event_snapshot(r.event),
        }
        for r in parsed.records
    ]
    projection = assemble_trace(
        parsed, compress_trivial=compress, loosen_boundary=loosen
    )
    receipts, reconciles = projection.hears, projection.reconciles
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
    with pytest.raises(TraceError, match="range semantics"):
        assemble_trace(
            read_trace(case / "unsupported-in-reconcile.log"),
            compress_trivial=True,
            loosen_boundary=False,
        )
