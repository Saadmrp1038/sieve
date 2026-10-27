"""Full-object and metadata informers watch the same resource independently."""

import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from sieve_tool.analysis.analyze import (
    build_controller_event_graph,
    generate_test_plans,
)
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.plans import load_plan

ROOT = Path(__file__).resolve().parents[1]


def test_learning_and_planning_keep_parallel_secret_informers_separate(tmp_path):
    # Retained External Secrets learning run, with explicit provenance added to
    # the metadata receipts. Original event payloads and ordering are preserved.
    trace = tmp_path / "sieve-server.log"
    trace.write_bytes(
        gzip.decompress(
            (ROOT / "fixtures/traces/dual-informer/trace.log.gz").read_bytes()
        )
    )
    (tmp_path / "mask.json").write_text("{}")
    (tmp_path / "state.json").write_text(
        json.dumps({"secret/default/sieve-external-secret": {}})
    )
    context = SimpleNamespace(
        result_dir=str(tmp_path),
        oracle_dir=str(tmp_path),
        common_config=get_common_config(),
        controller_config=load_controller_config(ROOT / "controllers/external-secrets"),
        test_workload="delete-child",
    )
    graph = build_controller_event_graph(context, trace, tmp_path)
    receipts = {v.content.id: v.content for v in graph.controller_hear_vertices}
    for ids, observer in [([4, 8, 9], "metadata-informer"), ([5, 7, 10], "informer")]:
        assert [receipts[i].observed_by for i in ids] == [observer] * 3
        assert [receipts[i].prev_etype for i in ids] == [
            "NONE_TYPE",
            "Added",
            "Deleted",
        ]
        assert [receipts[i].signature_counter for i in ids] == [1, 1, 2]
        for i in ids:
            assert all(
                receipts[j].observed_by == observer for j in receipts[i].cancelled_by
            )

    generate_test_plans(context, "unobserved-state", graph)
    plans = [load_plan(path) for path in (tmp_path / "unobserved-state").glob("*.yaml")]
    assert {
        action.trigger.definitions[0].observation_point.by
        for plan in plans
        for action in plan.actions
    } == {"informer", "metadata-informer"}
