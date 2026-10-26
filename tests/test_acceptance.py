"""Real evidence checks and scoped failure handling for acceptance runs."""

import copy
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from sieve_tool.acceptance import runner
from sieve_tool.acceptance.evidence import (
    Phase,
    RunRecord,
    case_paths,
    verify,
)
from sieve_tool.acceptance.suite import (
    ImageManifest,
    build_inputs,
    image_references,
    load_suite,
    save_json,
    suite_directory,
)
from sieve_tool.common.config import get_common_config
from sieve_tool.plans import PauseControllerAction, load_plan
from sieve_tool.process import Cancelled, ProcessError, execution

ROOT = Path(__file__).resolve().parents[1]
SUITE = load_suite(ROOT)
SOURCE = {"revision": "a" * 40, "tree": "b" * 40}


def images():
    return ImageManifest(
        registry="acceptance-cache",
        built_revision="c" * 40,
        build_inputs={"go/client/common.go": "d" * 64},
        images={
            ref: "sha256:" + "e" * 64
            for ref in image_references(SUITE, "acceptance-cache")
        },
    )


def workload(directory):
    directory.mkdir(parents=True, exist_ok=True)
    save_json(
        directory / "process-outcome.json", {"status": "completed", "returncode": 0}
    )
    (directory / "workload.log").write_text("FINISH-SIEVE-TEST\n")
    save_json(
        directory / "workload-observations.json",
        {
            "final_consistency_checked": True,
            "kube_root_ca_configmap_present": True,
            "final_marker": "recreated",
            "original_cr_uid": "cr-old",
            "final_cr_uid": "cr-new",
            "final_secret_uid": "secret-new",
            "status_secret_uid": "secret-new",
            "secret_owner_uids": ["cr-new"],
        },
    )
    save_json(directory / "state.json", {"configmap/default/kube-root-ca.crt": {}})


def case_evidence(output, case):
    target = output / "plans" / case.relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(suite_directory(ROOT) / case.relative_path, target)
    directory, raw = case_paths(output, SUITE, case)
    workload(directory)
    plan = load_plan(target)
    server = "Sieve test coordinator finishes all actions\n"
    if case.family == "intermediate-state":
        server += "controller pod gets deleted\ncontroller pod 0 ready now\n"
    else:
        action = next(a for a in plan.actions if isinstance(a, PauseControllerAction))
        server += (
            f"file.go:1: Pause controller at {action.pause_at} {action.pause_scope}\n"
        )
        server += f"file.go:1: End pause controller at {action.pause_at} {action.pause_scope}\n"
    (directory / "sieve-server.log").write_text(server)
    logs = []
    for uid in ["pod-old", "pod-new"]:
        obj = {
            "metadata": {
                "name": uid,
                "uid": uid,
                "labels": {"sievetag": SUITE.controller},
            }
        }
        logs.append(
            "\t".join(
                [
                    "[SIEVE-API-EVENT]",
                    "ADDED",
                    "original",
                    "pod",
                    "default",
                    uid,
                    json.dumps(obj),
                ]
            )
            + "\n"
        )
    (directory / "apiserver1.log").write_text("".join(logs))
    save_json(
        directory / "resources-snapshot.json",
        {
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {
                        "uid": "pod-new",
                        "labels": {"sievetag": SUITE.controller},
                    },
                    "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                }
            ]
        },
    )
    raw.parent.mkdir(parents=True, exist_ok=True)
    save_json(
        raw,
        {
            SUITE.controller: {
                SUITE.workload: {
                    "test": {
                        "original-plan": {
                            "injection_completed": True,
                            "workload_completed": True,
                            "no_exception": True,
                            "number_errors": 0,
                            "detected_errors": [],
                            "test_plan_content": target.read_text(),
                            "execution": {"status": "completed", "returncode": 0},
                        }
                    }
                }
            }
        },
    )
    return directory, raw


@pytest.fixture
def completed(tmp_path):
    record = RunRecord(
        selection="smoke",
        source=SOURCE,
        images=images(),
        runtime_registry="sieve-acceptance-run-" + "0" * 32,
        status="completed",
        aliases_removed=True,
    )
    for mode in ["learn", "learn_prev"]:
        workload(tmp_path / "learn" / SUITE.controller / SUITE.workload / mode)
    workload(tmp_path / "vanilla" / SUITE.controller / SUITE.workload / "vanilla")
    for case in SUITE.select("smoke"):
        case_evidence(tmp_path, case)
    record.phases = [
        Phase(name=name, log=name + ".log", returncode=0)
        for name in ["vanilla", "learn", *(c.name for c in SUITE.select("smoke"))]
    ]
    save_json(tmp_path / "run.json", record.model_dump(mode="json"))
    (tmp_path / "DONE").write_text("completed\n")
    return tmp_path


def test_completed_evidence_is_verified_without_modifying_raw_files(completed):
    before = {
        p.relative_to(completed): p.read_bytes()
        for p in completed.rglob("*")
        if p.is_file()
    }
    result = verify(completed, SUITE)
    assert result["plans_executed"] == 2 and result["observed_restarts"] == 1
    assert result["blocked_and_released_reads"] == 1 and result["raw_alarms"] == 0
    assert before == {
        p.relative_to(completed): p.read_bytes()
        for p in completed.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "failure",
    [
        "alarm",
        "nonzero",
        "no-release",
        "wrong-scope",
        "missing-ca",
        "not-ready",
        "same-pod",
        "bad-owner",
        "different-plan",
        "missing-phase",
        "no-done",
        "alias-cleanup",
    ],
)
def test_reported_success_cannot_hide_missing_or_bad_evidence(completed, failure):
    restart = next(
        c for c in SUITE.cases if c.smoke and c.family == "intermediate-state"
    )
    missed = next(c for c in SUITE.cases if c.smoke and c.family == "unobserved-state")
    directory, raw = case_paths(completed, SUITE, restart)
    if failure == "alarm":
        data = json.loads(raw.read_text())
        data[SUITE.controller][SUITE.workload]["test"]["original-plan"][
            "detected_errors"
        ] = ["raw alarm"]
        save_json(raw, data)
    elif failure == "nonzero":
        save_json(
            directory / "process-outcome.json", {"status": "completed", "returncode": 7}
        )
    elif failure in {"no-release", "wrong-scope"}:
        directory, _ = case_paths(completed, SUITE, missed)
        path = directory / "sieve-server.log"
        text = path.read_text()
        text = (
            "".join(
                line
                for line in text.splitlines(keepends=True)
                if ": End pause" not in line
            )
            if failure == "no-release"
            else text.replace(
                "secret/default/sieve-generated-secret", "secret/default/unrelated"
            )
        )
        path.write_text(text)
    elif failure == "missing-ca":
        save_json(directory / "state.json", {})
    elif failure == "not-ready":
        path = directory / "resources-snapshot.json"
        path.write_text(path.read_text().replace('"True"', '"False"'))
    elif failure == "same-pod":
        path = directory / "apiserver1.log"
        path.write_text(path.read_text().replace("pod-new", "pod-old"))
    elif failure == "bad-owner":
        path = directory / "workload-observations.json"
        data = json.loads(path.read_text())
        data["secret_owner_uids"] = []
        save_json(path, data)
    elif failure == "different-plan":
        path = completed / "plans" / restart.relative_path
        path.write_text(path.read_text() + "# changed\n")
    elif failure in {"missing-phase", "alias-cleanup"}:
        path = completed / "run.json"
        data = json.loads(path.read_text())
        if failure == "missing-phase":
            data["phases"].pop()
        else:
            data["aliases_removed"] = False
        save_json(path, data)
    elif failure == "no-done":
        (completed / "DONE").unlink()
    with pytest.raises((ValueError, OSError)):
        verify(completed, SUITE)


def test_smoke_plans_keep_the_existing_reviewed_fault_behavior():
    for case, fixture in [
        (
            next(
                c for c in SUITE.cases if c.smoke and c.family == "intermediate-state"
            ),
            "restart.yaml",
        ),
        (
            next(c for c in SUITE.cases if c.smoke and c.family == "unobserved-state"),
            "missed-state.yaml",
        ),
    ]:
        assert (
            load_plan(suite_directory(ROOT) / case.relative_path).to_dict()
            == load_plan(ROOT / "fixtures/plans" / fixture).to_dict()
        )


def test_budget_honors_partial_and_workload_overrides():
    common = get_common_config()
    common.process_timeouts = {"workload": 2700}
    assert runner._single_budget(common) > 2700
    assert (
        runner._single_budget(common, {"workload": 3600})
        - runner._single_budget(common)
        == 900
    )


def test_acceptance_cannot_disable_oracle_checks():
    common = get_common_config()
    common.end_state_check_enabled = False
    with pytest.raises(ValueError, match="end_state_check_enabled"):
        runner._settings(common)


@pytest.mark.parametrize(
    "failure", ["stage", "cancel", "partial-tag", "image-mismatch", "existing-cluster"]
)
def test_failures_clean_only_owned_aliases_and_keep_cached_images(
    tmp_path, monkeypatch, failure
):
    manifest = images()
    path = tmp_path / "images.json"
    save_json(path, manifest.model_dump(mode="json"))
    monkeypatch.setattr(runner, "_source", lambda *args: SOURCE)
    monkeypatch.setattr(runner, "build_inputs", lambda root: manifest.build_inputs)
    monkeypatch.setattr(runner, "verify_revision", lambda *args: SOURCE)
    tags = dict(manifest.images)
    tags["unrelated:latest"] = "sha256:" + "f" * 64
    original = copy.deepcopy(tags)
    if failure == "image-mismatch":
        tags[next(iter(manifest.images))] = "sha256:" + "0" * 64
    removed = []

    def command(argv, **kwargs):
        stdout = ""
        if argv[:3] == ["kind", "get", "clusters"]:
            stdout = "kind\n" if failure == "existing-cluster" else ""
        elif argv[:3] == ["docker", "image", "inspect"]:
            stdout = tags[argv[-1]]
        elif argv[:3] == ["docker", "image", "tag"]:
            tags[argv[-1]] = argv[-2]
            if failure == "partial-tag":
                raise ProcessError("tag transport failed", 1, None)
        elif argv[:3] == ["docker", "image", "ls"]:
            stdout = tags.get(argv[-1].removeprefix("reference="), "")
        elif argv[:3] == ["docker", "image", "rm"]:
            removed.append(argv[-1])
            del tags[argv[-1]]
        else:
            raise AssertionError(argv)
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(runner, "run", command)

    def stage(*args):
        raise (
            Cancelled(15)
            if failure == "cancel"
            else ProcessError("stage failed", 7, None)
        )

    monkeypatch.setattr(runner, "_stage", stage)
    expected = (ProcessError, ValueError, Cancelled)
    output = tmp_path / "results"
    with pytest.raises(expected):
        runner.execute(
            ROOT, SOURCE["revision"], path, "smoke", output, get_common_config()
        )
    assert all(tag.startswith("sieve-acceptance-run-") for tag in removed)
    assert not any(tag.startswith("sieve-acceptance-run-") for tag in tags)
    assert tags["unrelated:latest"] == original["unrelated:latest"]
    if failure != "image-mismatch":
        assert tags == original
    record = json.loads((output / "run.json").read_text())
    assert record["status"] == ("cancelled" if failure == "cancel" else "failed")
    assert (output / "DONE").read_text().strip() == record["status"]


def test_build_fingerprint_is_deterministic_and_excludes_runtime_results(tmp_path):
    with execution(log_dir=tmp_path / "commands"):
        first = build_inputs(ROOT)
        second = build_inputs(ROOT)
    assert first == second and "go/client/common.go" in first
    assert not any(name.startswith(("runs/", ".cache/")) for name in first)


def test_child_deadline_leaves_time_for_its_bounded_cleanup(tmp_path):
    import sys

    from sieve_tool.process import ProcessTimeout

    marker = tmp_path / "child-cleaned"
    script = """import signal,sys,time
from pathlib import Path
def stop(signum, frame):
    time.sleep(0.1)
    Path(sys.argv[1]).write_text('cleaned')
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
time.sleep(60)
"""
    record = RunRecord(
        selection="smoke",
        source=SOURCE,
        images=images(),
        runtime_registry="sieve-acceptance-run-" + "0" * 32,
    )
    with execution({"terminate": 0.01}, tmp_path / "commands"):
        with pytest.raises(ProcessTimeout):
            runner._stage(
                record,
                tmp_path,
                "child-cleanup",
                [sys.executable, "-c", script, str(marker)],
                1.0,
                ROOT,
                {},
                2.0,
            )
    assert marker.read_text() == "cleaned"
    assert record.phases[0].returncode is None


def test_stale_image_inputs_fail_before_docker_or_experiment(tmp_path, monkeypatch):
    manifest = images()
    path = tmp_path / "images.json"
    save_json(path, manifest.model_dump(mode="json"))
    monkeypatch.setattr(runner, "_source", lambda *args: SOURCE)
    monkeypatch.setattr(
        runner, "build_inputs", lambda root: {"go/client/common.go": "0" * 64}
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("Docker must not be contacted for stale build inputs")

    monkeypatch.setattr(runner, "run", unexpected)
    with pytest.raises(ValueError, match="build inputs"):
        runner.execute(
            ROOT,
            SOURCE["revision"],
            path,
            "smoke",
            tmp_path / "results",
            get_common_config(),
        )
    assert not (tmp_path / "results").exists()
