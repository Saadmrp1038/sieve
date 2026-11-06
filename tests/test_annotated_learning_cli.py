"""Learning's public CLI preserves invocation bounds around opaque API effects."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FRAME = "example.(*Reconciler).Reconcile"


def postprocess(tmp_path, trace):
    output = tmp_path / "output"
    case = output / "kustomize-controller/prune-retry/learn"
    case.mkdir(parents=True)
    (case / "sieve-server.log").write_text(trace)
    (case / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    (case / "process-outcome.json").write_text('{"status":"completed","returncode":0}')
    reference = tmp_path / "oracles/kustomize-controller/prune-retry"
    reference.mkdir(parents=True)
    (reference / "mask.json").write_text("{}")
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps(
            {
                "effective_updates_pruning_enabled": False,
                "nondeterministic_pruning_enabled": False,
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sieve_tool",
            "--repo",
            str(ROOT),
            "--config",
            str(config),
            "learn",
            "-c",
            "kustomize-controller",
            "-w",
            "prune-retry",
            "--output",
            str(output),
            "--oracle-root",
            str(reference.parents[1]),
            "--postprocess",
            "--no-build-oracle",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, case


def test_learning_generates_restart_plans_for_interleaved_annotated_invocations(
    tmp_path,
):
    # Two overlapping workers share a controller instance. Their next invocation
    # follows both API-only effects. The earliest receipt is outside that next
    # invocation's previous checkpoint; there are exactly five eligible hear/effect
    # pairs (2 + 2 + 1), rather than six if API-only rounds are treated as trivial.
    def hear(number, name):
        return (
            f"[SIEVE-BEFORE-HEAR]\t{number}\tAdded\tconfigmap\t"
            f'{{"metadata":{{"name":"{name}","namespace":"default"}}}}\n'
            f"[SIEVE-AFTER-HEAR]\t{number}\n"
        )

    def begin(number):
        return f"[SIEVE-BEFORE-RECONCILE]\t{FRAME}\t{number}\tSIEVE-V2:receiver\n"

    def end(number):
        return f"[SIEVE-AFTER-RECONCILE]\t{FRAME}\t{number}\tSIEVE-V2:receiver\n"

    def after(call, invocation):
        return (
            f"[SIEVE-AFTER-ANNOTATED-API-INVOCATION]\t{call}\texample/api\tclient.go\t"
            f"*Client\tDelete\t{FRAME}\tSIEVE-V2:{invocation}\n"
        )

    trace = (
        hear(1, "old")
        + begin(1)
        + begin(2)
        + hear(2, "current")
        + "[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]\t10\n"
        + "[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]\t20\n"
        + after(20, 2)
        + end(2)
        + after(10, 1)
        + end(1)
        + begin(3)
        + "[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]\t30\n"
        + after(30, 3)
        + end(3)
    )
    result, case = postprocess(tmp_path, trace)
    assert result.returncode == 0, result.stdout + result.stderr
    plans = [
        yaml.safe_load(p.read_text())
        for p in sorted((case / "intermediate-state").glob("*.yaml"))
    ]
    assert [
        p["actions"][0]["trigger"]["definitions"][0]["condition"]["occurrence"]
        for p in plans
    ] == [2, 1, 3]
    assert all(
        p["actions"][0]["trigger"]["definitions"][0]["observationPoint"]
        == {"when": "afterAnnotatedAPICall", "by": FRAME}
        for p in plans
    )
    assert "5 edges from controller_hear to controller_write" in result.stdout


def test_annotated_occurrences_match_the_runtime_selector_and_return_order(tmp_path):
    def invocation(number, frame, module):
        return (
            f"[SIEVE-BEFORE-RECONCILE]\t{frame}\t{number}\tSIEVE-V2:receiver\n"
            f"[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]\t{number}\n"
            f"[SIEVE-AFTER-ANNOTATED-API-INVOCATION]\t{number}\t{module}\tclient.go\t"
            f"*Client\tDelete\t{frame}\tSIEVE-V2:{number}\n"
            f"[SIEVE-AFTER-RECONCILE]\t{frame}\t{number}\tSIEVE-V2:receiver\n"
        )

    trace = (
        invocation(1, FRAME, "example/api")
        + invocation(2, "other.Reconcile", "example/api")
        + invocation(3, FRAME, "other/api")
        + invocation(4, FRAME, "example/api")
    )
    result, case = postprocess(tmp_path, trace)
    assert result.returncode == 0, result.stdout + result.stderr
    plans = [
        yaml.safe_load(p.read_text())
        for p in sorted((case / "intermediate-state").glob("*.yaml"))
    ]
    assert [
        p["actions"][0]["trigger"]["definitions"][0]["condition"]["occurrence"]
        for p in plans
    ] == [1, 1, 1, 2]


@pytest.mark.parametrize(
    "malformation",
    [
        "duplicate_start",
        "duplicate_end",
        "missing_start",
        "inactive_invocation",
        "start_before_invocation",
    ],
)
def test_learning_rejects_malformed_annotated_pairs_before_generating_plans(
    tmp_path, malformation
):
    begin = f"[SIEVE-BEFORE-RECONCILE]\t{FRAME}\t1\tSIEVE-V2:receiver\n"
    start = "[SIEVE-BEFORE-ANNOTATED-API-INVOCATION]\t7\n"
    after = (
        f"[SIEVE-AFTER-ANNOTATED-API-INVOCATION]\t7\texample/api\tclient.go\t"
        f"*Client\tDelete\t{FRAME}\tSIEVE-V2:1\n"
    )
    end = f"[SIEVE-AFTER-RECONCILE]\t{FRAME}\t1\tSIEVE-V2:receiver\n"
    trace = {
        "duplicate_start": begin + start + start + after + end,
        "duplicate_end": begin + start + after + after + end,
        "missing_start": begin + after + end,
        "inactive_invocation": begin + start + end + after,
        "start_before_invocation": start + begin + after + end,
    }[malformation]
    result, case = postprocess(tmp_path, trace)
    assert result.returncode == 1
    assert "sieve-server.log:" in result.stdout + result.stderr
    assert not list(case.rglob("*-test-plan-*.yaml"))
