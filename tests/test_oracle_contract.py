"""Reviewed oracle outputs captured before separating comparisons from I/O."""

import copy
import gzip
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from sieve_tool.common.common import TestContext as RunContext
from sieve_tool.common.common import sieve_modes
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.oracle.oracle import check, create_differential_oracles
from sieve_tool.runtime.runner import save_run_result

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "fixtures/oracle-contracts"
FIELDS = (
    "injection_completed",
    "workload_completed",
    "common_errors",
    "end_state_errors",
    "history_errors",
    "no_exception",
    "exception_message",
)


def context(root, directory, reference, workload, common, controller, plan=None):
    return RunContext(
        "kubernetes-secret-generator",
        str(ROOT / "controllers/kubernetes-secret-generator"),
        workload,
        sieve_modes.TEST if plan else sieve_modes.LEARN,
        True,
        plan is None,
        str(plan or ""),
        str(plan or ""),
        str(root),
        str(directory),
        str(reference),
        "unused",
        "unused",
        1,
        0,
        False,
        common,
        controller,
    )


def write_artifacts(directory, artifacts):
    directory.mkdir(parents=True)
    for name, content in artifacts.items():
        (directory / name).write_text(
            json.dumps(content) if name.endswith(".json") else content
        )


def snapshot(group, root):
    common = get_common_config()
    common.generate_debugging_information_enabled = False
    for name, value in group.get("common", {}).items():
        setattr(common, name, value)
    controller = load_controller_config(
        ROOT / "controllers/kubernetes-secret-generator"
    )
    for name, value in group.get("controller", {}).items():
        setattr(controller, name, value)
    reference = root / "reference"
    reference.mkdir(parents=True)
    for name, artifacts in group["baselines"].items():
        write_artifacts(root / name, artifacts)
    create_differential_oracles(
        context(root, root / "learn", reference, group["workload"], common, controller)
    )
    result = {
        "reference": {
            p.name: json.loads(p.read_text()) for p in reference.glob("*.json")
        },
        "tests": {},
    }
    for case in group["tests"]:
        directory = root / case["name"]
        write_artifacts(directory, case["artifacts"])
        plan = root / (case["name"] + ".yaml")
        plan.write_text(yaml.safe_dump(case["plan"], sort_keys=True))
        ctx = context(
            root, directory, reference, group["workload"], common, controller, plan
        )
        verdict = check(ctx)
        with (
            patch("sieve_tool.runtime.runner.time.time", return_value=117),
            patch(
                "sieve_tool.runtime.runner.socket.gethostname",
                return_value="fixture-host",
            ),
        ):
            save_run_result(ctx, verdict, 100)
        raw = root / (
            "kubernetes-secret-generator-"
            + group["workload"]
            + "-"
            + plan.name
            + ".json"
        )
        serialized = next(
            iter(
                json.loads(raw.read_text())[ctx.controller][ctx.test_workload][
                    "test"
                ].values()
            )
        )
        result["tests"][case["name"]] = {
            "verdict": {name: copy.deepcopy(getattr(verdict, name)) for name in FIELDS},
            "serialized": serialized,
        }
    return result


@pytest.mark.parametrize("path", sorted(CORPUS.glob("*.json.gz")), ids=lambda p: p.stem)
def test_oracle_outputs_match_the_previous_engine(tmp_path, path):
    group = json.loads(gzip.decompress(path.read_bytes()))
    original = copy.deepcopy(group)
    assert snapshot(group, tmp_path) == group["expected"]
    assert group == original
