"""Coverage counts actual, declared placements separately from generated cases."""

import json


def test_public_report_distinguishes_two_placements_without_claiming_executions(
    tmp_path,
):
    from sieve_tool.testing.metadata_cases import explore
    from sieve_tool.testing.sequence_report import summarize

    cases = [
        c
        for c in explore()
        if c.operator == "external-secrets"
        and c.channel == "labels"
        and c.history_name == "remove-readd"
    ]
    paths = []
    for case in cases:
        path = tmp_path / case.workload
        path.mkdir()
        (path / "metadata-plan.json").write_text(json.dumps(case.metadata_plan()))
        paths.append(path)
    result = summarize(paths)
    assert result["status"] == "not_attempted"
    assert result["counts"]["selected_cases"] == 2
    assert result["counts"]["generated_histories"] == 4
    assert (
        result["counts"]["attempted_executions"]
        == result["counts"]["verified_overlap_executions"]
        == 0
    )
    assert all(c["valid_evidence"] for c in result["cases"])


import pytest


@pytest.fixture
def case_evidence(tmp_path):
    def build(case, mode="hold", name="run"):
        directory = tmp_path / name
        directory.mkdir()
        kind = "Certificate" if case.operator == "cert-manager" else "ExternalSecret"
        values = (
            (None, "one", None, "two")
            if case.history_name == "remove-readd"
            else (None, "one", "two", "one")
        )
        ops = (
            ("add", "remove", "add")
            if case.history_name == "remove-readd"
            else ("add", "change", "change")
        )
        accepted = []
        for i, value in enumerate(values):
            metadata = {
                "labels": {"sieve.testing/keep": "retained"},
                "annotations": {"sieve.testing/keep": "retained"},
            }
            if value is not None:
                metadata[case.channel]["sieve.testing/history"] = value
            obj = {
                "kind": kind,
                "metadata": {
                    "uid": "subject",
                    "name": "sieve-metadata-subject",
                    "namespace": "default",
                    "generation": i + 1,
                    "resourceVersion": str(i + 10),
                    "labels": {},
                },
                "spec": {"secretTemplate": metadata}
                if kind == "Certificate"
                else {"target": {"template": {"metadata": metadata}}},
            }
            if i:
                obj["metadata"]["labels"]["sieve.testing/state"] = (
                    f"metadata-{i}-{ops[i - 1]}"
                )
            accepted.append(
                {
                    "role": "subject",
                    "operation": "initial" if not i else ops[i - 1],
                    "response": obj,
                }
            )
        schedule = case.plan(mode).delivery_overlap
        held = {
            "mode": mode,
            "state": "held",
            "resourceKey": schedule.resource_key,
            "reconciler": schedule.reconciler,
            "invocationID": "selected",
            "instanceID": "instance",
            "uid": "subject",
            "generation": case.hold_after_step + 1,
            "resourceVersion": str(case.hold_after_step + 10),
            "releasedGeneration": 0,
            "releasedResourceVersion": "",
        }
        completed = {
            **held,
            "state": "completed",
            "releasedGeneration": case.release_after_step + 1,
            "releasedResourceVersion": str(case.release_after_step + 10),
        }
        gate = {"initial": {"mode": mode}}
        if mode == "hold":
            gate.update(held=held, completed=completed)
        obs = {"status": "passed", "accepted": accepted, "delivery_overlap": gate}
        lines = []

        def marker(value):
            return "[SIEVE-DELIVERY-OVERLAP]\t" + json.dumps(value) + "\n"

        for i, a in enumerate(accepted):
            obj = a["response"]
            if i:
                lines += [
                    f"[SIEVE-BEFORE-HEAR]\t{i}\tUpdated\t{kind.lower()}\t{json.dumps(obj)}\n",
                    f"[SIEVE-AFTER-HEAR]\t{i}\n",
                ]
            if mode == "hold" and i == case.release_after_step:
                lines += [
                    marker({**completed, "state": "released"}),
                    f"[SIEVE-AFTER-RECONCILE]\t{schedule.reconciler}\tselected\tSIEVE-V2:instance\n",
                    marker(completed),
                ]
            call = (
                "selected"
                if mode == "hold" and i == case.hold_after_step
                else "call-" + str(i)
            )
            lines += [
                f"[SIEVE-BEFORE-RECONCILE]\t{schedule.reconciler}\t{call}\tSIEVE-V2:instance\n",
                f"[SIEVE-AFTER-CACHE-READ]\tGet\t{kind.lower()}\tdefault\tsieve-metadata-subject\t{schedule.reconciler}\tNoError\t{json.dumps(obj)}\tSIEVE-V2:{call}\n",
            ]
            if call == "selected":
                lines.append(marker(held))
            else:
                lines.append(
                    f"[SIEVE-AFTER-RECONCILE]\t{schedule.reconciler}\t{call}\tSIEVE-V2:instance\n"
                )
        (directory / "sieve-server.log").write_text("".join(lines))
        (directory / "metadata-plan.json").write_text(json.dumps(case.metadata_plan()))
        (directory / "metadata-observations.json").write_text(json.dumps(obs))
        for file in ("process-outcome.json", "injection-process-outcome.json"):
            (directory / file).write_text('{"status":"completed","returncode":0}')
        (directory / "semantic-outcome.json").write_text(
            '{"schema_version":1,"checks":[{"name":"fixture","passed":true}]}'
        )
        (directory / "workload.log").write_text("FINISH-SIEVE-TEST\n")
        return directory

    return build


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("history", ["remove-readd", "restore-value"])
@pytest.mark.parametrize("hold", [1, 2])
@pytest.mark.parametrize("mode", ["observe", "hold"])
def test_new_report_requires_only_declared_fault_placements_and_preserves_controls(
    case_evidence, operator, history, hold, mode
):
    from sieve_tool.testing.metadata_cases import MetadataCase
    from sieve_tool.testing.sequence_report import summarize

    case = MetadataCase(operator, "annotations", history, hold)
    directory = case_evidence(case, mode)
    report = summarize([directory])
    row = report["cases"][0]
    assert report["status"] == "satisfied", row
    assert row["mode"] == mode and row["all_inputs_observed"]
    assert row["overlap_verified"] == (mode == "hold")
    assert report["counts"]["required_overlap_executions"] == int(mode == "hold")


def test_fault_proof_rejects_a_tampered_frozen_placement(case_evidence):
    from sieve_tool.fault_proofs import audit_fault
    from sieve_tool.results import TestResult as RunResult
    from sieve_tool.testing.metadata_cases import MetadataCase

    case = MetadataCase("external-secrets", "labels", "remove-readd", 1)
    path = case_evidence(case)
    result = RunResult(True, True, [], [], [], True, "")
    assert audit_fault(path, case.plan("hold"), result)["physically_verified"]
    plan_file = path / "metadata-plan.json"
    value = json.loads(plan_file.read_text())
    value["placement"]["hold_after_step"] = 2
    plan_file.write_text(json.dumps(value))
    assert not audit_fault(path, case.plan("hold"), result)["physically_verified"]


def test_campaign_report_pairs_controls_and_counts_only_selected_verified_placements(
    tmp_path, case_evidence
):
    from sieve_tool.testing.metadata_cases import explore
    from sieve_tool.testing.metadata_explorer import main

    frozen = tmp_path / "frozen"
    assert main(["plan", "--max-cases", "2", "--output", str(frozen)]) == 0
    case = explore(max_cases=1)[0]
    fault = case_evidence(case, name="fault")
    control = case_evidence(case, mode="observe", name="control")
    outputs = [tmp_path / (s + ".json") for s in ("empty", "unpaired", "paired")]
    for paths, output, attempted, covered in (
        ([], outputs[0], 0, 0),
        ([fault], outputs[1], 1, 0),
        ([control, fault], outputs[2], 2, 1),
    ):
        args = [
            "report",
            "--plan",
            str(frozen / "exploration.json"),
            "--output",
            str(output),
        ]
        for path in paths:
            args += ["--case", str(path)]
        assert main(args) == 0
        report = json.loads(output.read_text())
        assert report["counts"]["selected_cases"] == 2
        assert report["counts"]["attempted_executions"] == attempted
        assert report["counts"]["paired_verified_placements"] == covered
        assert report["status"] == ("not_attempted" if not attempted else "incomplete")
    result = json.loads(outputs[-1].read_text())
    assert result["counts"]["verified_overlap_executions"] == 1
    assert result["counts"]["covered_operation_pairs"] == 1
    assert len(result["not_covered_case_ids"]) == 1
    assert "exploration.json" in result["evidence_sha256"]


def test_observe_control_cannot_hide_an_injected_hold(case_evidence):
    from sieve_tool.testing.metadata_cases import MetadataCase
    from sieve_tool.testing.sequence_report import summarize

    directory = case_evidence(
        MetadataCase("external-secrets", "labels", "restore-value", 2)
    )
    p = directory / "metadata-observations.json"
    data = json.loads(p.read_text())
    data["delivery_overlap"]["initial"]["mode"] = "observe"
    p.write_text(json.dumps(data))
    row = summarize([directory])["cases"][0]
    assert not row["valid_evidence"]
    assert "unexpected_overlap_in_control" in row["reasons"]


def test_campaign_report_rejects_changed_manifest_counters_and_preserves_inputs(
    tmp_path,
):
    from sieve_tool.testing.metadata_explorer import main

    frozen = tmp_path / "frozen"
    main(["plan", "--max-cases", "1", "--output", str(frozen)])
    manifest = frozen / "exploration.json"
    value = json.loads(manifest.read_text())
    value["generated_cases"] = 999
    manifest.write_text(json.dumps(value))
    before = manifest.read_bytes()
    with pytest.raises(SystemExit) as error:
        main(
            [
                "report",
                "--plan",
                str(manifest),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
    assert error.value.code == 2 and manifest.read_bytes() == before
    assert not (tmp_path / "report.json").exists()


def test_partial_failed_history_still_counts_as_a_declared_fault_attempt(
    tmp_path, case_evidence
):
    from sieve_tool.testing.metadata_cases import explore
    from sieve_tool.testing.metadata_explorer import main

    frozen = tmp_path / "frozen"
    main(["plan", "--max-cases", "1", "--output", str(frozen)])
    case = explore(max_cases=1)[0]
    directory = case_evidence(case)
    p = directory / "metadata-observations.json"
    value = json.loads(p.read_text())
    value["accepted"] = value["accepted"][:2]
    value["status"] = "overlap_failed"
    p.write_text(json.dumps(value))
    output = tmp_path / "report.json"
    main(
        [
            "report",
            "--plan",
            str(frozen / "exploration.json"),
            "--case",
            str(directory),
            "--output",
            str(output),
        ]
    )
    result = json.loads(output.read_text())
    assert result["counts"]["attempted_executions"] == 1
    assert result["counts"]["fault_attempts"] == 1
    assert result["counts"]["paired_verified_placements"] == 0
    assert result["status"] == "incomplete"


def test_physical_placement_is_retained_when_semantics_fail_and_later_reads_are_missing(
    tmp_path, case_evidence
):
    from sieve_tool.testing.metadata_cases import explore
    from sieve_tool.testing.metadata_explorer import main

    frozen = tmp_path / "frozen"
    main(["plan", "--max-cases", "1", "--output", str(frozen)])
    case = explore(max_cases=1)[0]
    control = case_evidence(case, mode="observe", name="control")
    fault = case_evidence(case, name="fault")
    p = fault / "sieve-server.log"
    lines = p.read_text().splitlines()
    p.write_text(
        "\n".join(
            line
            for line in lines
            if not ("[SIEVE-AFTER-CACHE-READ]" in line and "SIEVE-V2:call-3" in line)
        )
        + "\n"
    )
    p = fault / "metadata-observations.json"
    value = json.loads(p.read_text())
    value["status"] = "semantic_violation"
    p.write_text(json.dumps(value))
    (fault / "semantic-outcome.json").write_text(
        '{"schema_version":1,"checks":[{"name":"fixture","passed":false}]}'
    )
    (fault / "process-outcome.json").write_text('{"status":"failed","returncode":1}')
    (fault / "workload.log").write_text("metadata did not converge\n")
    output = tmp_path / "report.json"
    main(
        [
            "report",
            "--plan",
            str(frozen / "exploration.json"),
            "--case",
            str(control),
            "--case",
            str(fault),
            "--output",
            str(output),
        ]
    )
    result = json.loads(output.read_text())
    assert result["status"] == "incomplete"
    assert (
        result["counts"]["verified_overlap_executions"]
        == result["counts"]["paired_verified_placements"]
        == 1
    )
    assert result["counts"]["reported_semantic_failures"] == 1
