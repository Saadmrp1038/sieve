"""A completed pause is effective only when the returned read skips its input."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from sieve_tool.oracle.oracle import validate_run

ROOT = Path(__file__).resolve().parents[1]
KEY = "secret/default/example"
FRAME = "fixture.Reconcile"


@pytest.mark.parametrize(
    "case, expected",
    [
        ("later", True),
        ("intermediate", False),
        ("missing_read", False),
        ("missing_cache", False),
    ],
)
def test_verdict_requires_an_observed_overwrite_and_returned_later_state(
    tmp_path, case, expected
):
    def obj(phase):
        return {
            "kind": "Secret",
            "metadata": {
                "name": "example",
                "namespace": "default",
                "uid": "live-child",
                "resourceVersion": str("ABCD".index(phase) + 1),
                "labels": {"sieve.testing/state": phase},
            },
        }

    source = {
        "schema_version": 1,
        "status": "completed",
        "workload": "independent-updates",
        "resource_key": KEY,
        "updates": [
            {
                "producer": "workload",
                "state": {"metadata": {"labels": {"sieve.testing/state": p}}},
                "response": obj(p),
            }
            for p in "ABCD"
        ],
    }
    (tmp_path / "workload-updates.json").write_text(json.dumps(source))
    plan = yaml.safe_load((ROOT / "fixtures/plans/independent-state.yaml").read_text())
    plan["actions"][0]["pauseBy"] = FRAME
    lines = []

    def receipt(phase, observer):
        lines.append(
            "file.go:1: NotifyTestAfterControllerRecv\tUpdated\t"
            + KEY
            + "\t"
            + json.dumps(obj(phase))
            + "\t"
            + observer
        )

    for observer in ("metadata-informer", "informer"):
        receipt("B", observer)
    lines.append("file.go:1: Pause controller at beforeControllerRead " + KEY)
    receipt("C", "metadata-informer")
    if case != "missing_cache":
        receipt("C", "informer")
    lines.append("file.go:1: End pause controller at beforeControllerRead " + KEY)
    if case != "missing_read":
        value = obj("B" if case == "intermediate" else "C")
        lines.append(
            "\t".join(
                [
                    "[SIEVE-AFTER-CACHE-READ]",
                    "Get",
                    "secret",
                    "default",
                    "example",
                    FRAME,
                    "NoError",
                    json.dumps(value),
                ]
            )
        )
    lines.append("file.go:1: NotifyTestAfterControllerReadPause\tGet\t" + KEY + "\t")
    lines.append("Sieve test coordinator finishes all actions")
    (tmp_path / "sieve-server.log").write_text("\n".join(lines) + "\n")
    (tmp_path / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (tmp_path / "process-outcome.json").write_text(
        '{"status":"completed","returncode":0}'
    )
    context = SimpleNamespace(result_dir=str(tmp_path), test_plan_content=plan)
    assert validate_run(context, require_missed_state=True) == (expected, True)
    proof = json.loads((tmp_path / "missed-state-proof.json").read_text())
    assert proof["verified"] is expected
