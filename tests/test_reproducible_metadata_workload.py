"""Workload sequencing is driven through its CLI and external process responses."""

import json
from pathlib import Path

import pytest

from tests.test_metadata_workloads import invoke
from tests.test_metadata_workloads import metadata_cluster as metadata_cluster


@pytest.fixture
def overlap_cluster(tmp_path, metadata_cluster):
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        + r"""
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
assert args[:4]==['exec','kind-control-plane','/sieve/coordinator/sieve-coordinator','overlap']
wait=args[4];path=Path(os.environ['METADATA_TEST_CLUSTER']);cluster=json.loads(path.read_text());op=os.environ['OVERLAP_TEST_OPERATOR'];kind='certificate' if op=='cert-manager' else 'externalsecret';key=kind+'/sieve-metadata-subject'
result={'mode':'hold','state':'armed','resourceKey':kind+'/default/sieve-metadata-subject','reconciler':'operator.Reconcile','invocationID':'chosen','instanceID':'receiver','uid':'','generation':0,'resourceVersion':'','releasedGeneration':0,'releasedResourceVersion':''}
if cluster.get('overlap_mode')=='observe':result.update(mode='observe',state='observing')
if wait!='status':
    parent=cluster['resources'][key];m=parent['metadata'];managed=parent['spec']['secretTemplate'] if kind=='certificate' else parent['spec']['target']['template']['metadata']
    if wait=='held':
        assert m['generation']==3 and m['labels']['sieve.testing/state']=='metadata-2-change' and managed['labels']['sieve.testing/history']=='two'
        result.update(state='held',uid=m['uid'],generation=3,resourceVersion=m['resourceVersion']);cluster['overlap_held']=result.copy()
    elif wait=='completed':
        assert m['generation']==4 and 'sieve.testing/history' not in managed['labels'];result=cluster['overlap_held'].copy();result.update(state='completed',releasedGeneration=4,releasedResourceVersion=m['resourceVersion'])
    else:raise SystemExit('unsupported gate')
bad=cluster.get('bad_overlap')
if wait=='held' and bad in ('uid','generation'):result[bad]='wrong' if bad=='uid' else True
if wait=='completed' and bad=='releasedResourceVersion':result[bad]='unacknowledged'
cluster.setdefault('overlap_queries',[]).append(wait);path.write_text(json.dumps(cluster));print(json.dumps(result))
"""
    )
    docker.chmod(0o755)
    return metadata_cluster


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_workload_waits_for_held_change_before_remove_and_completed_gate_before_success(
    tmp_path, overlap_cluster, operator
):
    environment = {**overlap_cluster, "OVERLAP_TEST_OPERATOR": operator}
    output = tmp_path / "gated/workload.log"
    result = invoke(
        operator, output, environment, "--delivery-overlap", schedule="overlap"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["delivery_overlap"]["held"]["generation"] == 3
    assert evidence["delivery_overlap"]["completed"]["releasedGeneration"] == 4
    assert evidence["delivery_overlap"]["completed"]["state"] == "completed"
    assert evidence["status"] == "passed"
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    state = json.loads(Path(environment["METADATA_TEST_CLUSTER"]).read_text())
    assert state["overlap_queries"] == ["status", "held", "completed"]


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_matched_observe_control_does_not_wait_for_a_hold(
    tmp_path, overlap_cluster, operator
):
    state = Path(overlap_cluster["METADATA_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["overlap_mode"] = "observe"
    state.write_text(json.dumps(cluster))
    output = tmp_path / "control/workload.log"
    result = invoke(
        operator,
        output,
        {**overlap_cluster, "OVERLAP_TEST_OPERATOR": operator},
        "--delivery-overlap",
        schedule="overlap",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(state.read_text())["overlap_queries"] == ["status"]
    assert (
        json.loads(output.with_name("metadata-observations.json").read_text())["status"]
        == "passed"
    )


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("bad", ["uid", "generation", "releasedResourceVersion"])
def test_mismatched_gate_retains_progress_and_never_reports_success(
    tmp_path, overlap_cluster, operator, bad
):
    state = Path(overlap_cluster["METADATA_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["bad_overlap"] = bad
    state.write_text(json.dumps(cluster))
    output = tmp_path / "bad/workload.log"
    result = invoke(
        operator,
        output,
        {**overlap_cluster, "OVERLAP_TEST_OPERATOR": operator},
        "--delivery-overlap",
        schedule="overlap",
    )
    assert result.returncode == 1
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == "overlap_failed"
    assert len(evidence["accepted"]) == (4 if bad == "releasedResourceVersion" else 3)
    assert "FINISH-SIEVE-TEST" not in output.read_text()
