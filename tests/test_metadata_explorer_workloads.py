"""Named cases execute through both public workload CLIs and Kubernetes replies."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_metadata_workloads import metadata_cluster as metadata_cluster

ROOT = Path(__file__).resolve().parents[1]


def invoke(operator, workload, output, environment, *options):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers" / operator / "workloads/workload.py"),
            workload,
            str(output),
            "--repair-timeout",
            "3",
            "--settle-seconds",
            "0",
            *options,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("channel", ["labels", "annotations"])
def test_named_case_plan_freezes_return_history_and_first_adjacent_placement(
    tmp_path, operator, channel
):
    workload = f"metadata-{channel}-remove-readd-at-1-overlap"
    output = tmp_path / "planned/workload.log"
    result = invoke(
        operator,
        workload,
        output,
        {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        "--plan-only",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(output.with_name("metadata-plan.json").read_text())
    assert plan["schema_version"] == 2 and plan["workers"] == 10
    assert plan["case_id"] == operator + "/" + workload
    assert plan["selected"] == {
        "channel": channel,
        "steps": [
            {"operation": "add", "value": "one"},
            {"operation": "remove", "value": None},
            {"operation": "add", "value": "two"},
        ],
    }
    assert plan["placement"] == {"hold_after_step": 1, "release_after_step": 2}
    assert not output.exists()


@pytest.fixture
def explorer_cluster(tmp_path, metadata_cluster):
    base = tmp_path / "kubectl-base"
    (tmp_path / "kubectl").rename(base)
    (tmp_path / "kubectl").write_text(
        f"#!{sys.executable}\n"
        + r"""
import copy,json,os,subprocess,sys
from pathlib import Path
p=Path(os.environ['METADATA_TEST_CLUSTER']);cfg=json.loads(os.environ['EXPLORER_CASE']);before=json.loads(p.read_text());args=sys.argv[1:];args=args[2:] if args[:2]==['--namespace','default'] else args
old=copy.deepcopy(before['resources'].get('secret/sieve-metadata-subject'))
r=subprocess.run([str(Path(__file__).with_name('kubectl-base')),*sys.argv[1:]],capture_output=True)
if r.returncode==0 and args[0]=='patch' and cfg['mode']=='hold':
 c=json.loads(p.read_text());parent=c['resources'][cfg['kind']+'/sieve-metadata-subject']
 if parent['metadata']['generation']==cfg['hold']+1 or c.get('stale_final') and parent['metadata']['generation']==4:
  if c.get('stale_final') and parent['metadata']['generation']==4:old['metadata'][cfg['channel']]['sieve.testing/history']='two'
  c['resources']['secret/sieve-metadata-subject']=old;p.write_text(json.dumps(c))
sys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);raise SystemExit(r.returncode)
"""
    )
    (tmp_path / "kubectl").chmod(0o755)
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json,os,sys
from pathlib import Path
p=Path(os.environ['METADATA_TEST_CLUSTER']);c=json.loads(p.read_text());cfg=json.loads(os.environ['EXPLORER_CASE']);args=sys.argv[1:];assert args[:4]==['exec','kind-control-plane','/sieve/coordinator/sieve-coordinator','overlap'];wait=args[4]
frame={'externalsecret':'github.com/external-secrets/external-secrets/pkg/controllers/externalsecret.(*Reconciler).Reconcile','certificate':'github.com/cert-manager/cert-manager/pkg/controller/certificates/issuing.(*controller).ProcessItem'}[cfg['kind']]
r={'mode':cfg['mode'],'state':'observing' if cfg['mode']=='observe' else 'armed','resourceKey':cfg['kind']+'/default/sieve-metadata-subject','reconciler':frame,'invocationID':'selected','instanceID':'instance','uid':'','generation':0,'resourceVersion':'','releasedGeneration':0,'releasedResourceVersion':''}
if wait!='status':
 parent=c['resources'][cfg['kind']+'/sieve-metadata-subject'];m=parent['metadata'];index=cfg['hold'] if wait=='held' else cfg['hold']+1;assert m['generation']==index+1
 operation=(['add','remove','add'] if cfg['history']=='remove-readd' else ['add','change','change'])[index-1];assert m['labels']['sieve.testing/state']==f'metadata-{index}-{operation}'
 if wait=='held':r.update(state='held',uid=m['uid'],generation=m['generation'],resourceVersion=m['resourceVersion']);c['held']=r.copy()
 elif wait=='completed':r=c['held'].copy();r.update(state='completed',releasedGeneration=m['generation'],releasedResourceVersion=m['resourceVersion'])
 else:raise SystemExit('bad query')
if wait=='held' and c.get('bad_gate'):r['uid']='unrelated'
c.setdefault('gate_events',[]).append(wait);p.write_text(json.dumps(c));print(json.dumps(r))
"""
    )
    docker.chmod(0o755)
    return metadata_cluster


def environment_for(cluster, operator, channel, history, hold, mode="hold"):
    return {
        **cluster,
        "EXPLORER_CASE": json.dumps(
            {
                "kind": "certificate"
                if operator == "cert-manager"
                else "externalsecret",
                "channel": channel,
                "history": history,
                "hold": hold,
                "mode": mode,
            }
        ),
    }


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_first_step_hold_releases_before_waiting_for_metadata_convergence(
    tmp_path, explorer_cluster, operator
):
    output = tmp_path / "run/workload.log"
    result = invoke(
        operator,
        "metadata-labels-remove-readd-at-1-overlap",
        output,
        environment_for(explorer_cluster, operator, "labels", "remove-readd", 1),
        "--delivery-overlap",
    )
    assert result.returncode == 0, result.stdout + result.stderr + output.read_text()
    obs = json.loads(output.with_name("metadata-observations.json").read_text())
    assert obs["delivery_overlap"]["held"]["generation"] == 2
    assert obs["delivery_overlap"]["completed"]["releasedGeneration"] == 3
    assert not any(s["accepted_count"] == 2 for s in obs["samples"])
    assert obs["status"] == "passed"


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("channel", ["labels", "annotations"])
@pytest.mark.parametrize("history", ["remove-readd", "restore-value"])
@pytest.mark.parametrize("hold", [1, 2])
@pytest.mark.parametrize("mode", ["observe", "hold"])
def test_every_bounded_case_executes_with_expected_final_metadata_and_fresh_equivalence(
    tmp_path, explorer_cluster, operator, channel, history, hold, mode
):
    output = tmp_path / "run/workload.log"
    result = invoke(
        operator,
        f"metadata-{channel}-{history}-at-{hold}-overlap",
        output,
        environment_for(explorer_cluster, operator, channel, history, hold, mode),
        "--delivery-overlap",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    obs = json.loads(output.with_name("metadata-observations.json").read_text())
    assert obs["status"] == "passed" and output.read_text() == "FINISH-SIEVE-TEST\n"
    operations = (
        ["initial", "add", "remove", "add", "fresh"]
        if history == "remove-readd"
        else ["initial", "add", "change", "change", "fresh"]
    )
    assert [a["operation"] for a in obs["accepted"]] == operations
    final = obs["samples"][-1]
    assert final["history_equivalence"]["status"] == "satisfied"
    expected = "two" if history == "remove-readd" else "one"
    for role in ("subject", "fresh"):
        metadata = final[role]["child"]["metadata"]
        assert metadata[channel]["sieve.testing/history"] == expected
        assert all(
            metadata[c]["sieve.testing/keep"] == "retained"
            for c in ("labels", "annotations")
        )
    state = json.loads(Path(explorer_cluster["METADATA_TEST_CLUSTER"]).read_text())
    assert state["gate_events"] == (
        ["status"] if mode == "observe" else ["status", "held", "completed"]
    )
    if mode == "hold":
        assert obs["delivery_overlap"]["held"]["generation"] == hold + 1
        assert obs["delivery_overlap"]["completed"]["releasedGeneration"] == hold + 2
    if hold == 1:
        assert not any(s["accepted_count"] == 2 for s in obs["samples"])


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_wrong_held_identity_keeps_acknowledgements_and_never_submits_release(
    tmp_path, explorer_cluster, operator
):
    state = Path(explorer_cluster["METADATA_TEST_CLUSTER"])
    data = json.loads(state.read_text())
    data["bad_gate"] = True
    state.write_text(json.dumps(data))
    output = tmp_path / "run/workload.log"
    result = invoke(
        operator,
        "metadata-labels-remove-readd-at-1-overlap",
        output,
        environment_for(explorer_cluster, operator, "labels", "remove-readd", 1),
        "--delivery-overlap",
    )
    assert result.returncode == 1
    obs = json.loads(output.with_name("metadata-observations.json").read_text())
    assert obs["status"] == "overlap_failed" and len(obs["accepted"]) == 2
    assert "FINISH-SIEVE-TEST" not in output.read_text()


def test_named_plan_only_exports_the_matched_observe_plan(tmp_path):
    from sieve_tool.plans import load_plan

    output = tmp_path / "planned/workload.log"
    result = invoke(
        "external-secrets",
        "metadata-labels-restore-value-at-2-overlap",
        output,
        {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        "--plan-only",
    )
    assert result.returncode == 0
    assert (
        load_plan(output.with_name("metadata-no-fault.yaml")).delivery_overlap.mode
        == "observe"
    )


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_restored_input_cannot_pass_with_stale_child_metadata(
    tmp_path, explorer_cluster, operator
):
    state = Path(explorer_cluster["METADATA_TEST_CLUSTER"])
    data = json.loads(state.read_text())
    data["stale_final"] = True
    state.write_text(json.dumps(data))
    output = tmp_path / "run/workload.log"
    result = invoke(
        operator,
        "metadata-labels-restore-value-at-2-overlap",
        output,
        environment_for(explorer_cluster, operator, "labels", "restore-value", 2),
        "--delivery-overlap",
    )
    assert result.returncode == 1
    obs = json.loads(output.with_name("metadata-observations.json").read_text())
    assert obs["status"] == "semantic_violation" and len(obs["accepted"]) == 4
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert (
        json.loads(output.with_name("semantic-outcome.json").read_text())["checks"][0][
            "passed"
        ]
        is False
    )


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_new_named_cases_require_exactly_ten_reconciliation_workers(
    tmp_path, explorer_cluster, operator
):
    state = Path(explorer_cluster["METADATA_TEST_CLUSTER"])
    data = json.loads(state.read_text())
    data["workers"] = 9
    state.write_text(json.dumps(data))
    output = tmp_path / "run/workload.log"
    result = invoke(
        operator,
        "metadata-labels-remove-readd-at-2-overlap",
        output,
        environment_for(explorer_cluster, operator, "labels", "remove-readd", 2),
        "--delivery-overlap",
    )
    assert result.returncode == 1
    assert (
        json.loads(output.with_name("metadata-observations.json").read_text())["status"]
        == "configuration_failed"
    )
    assert "FINISH-SIEVE-TEST" not in output.read_text()
