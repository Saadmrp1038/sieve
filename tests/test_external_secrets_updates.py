"""Independent workload writes remain possible while a controller is paused."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def update_cluster(tmp_path):
    state = tmp_path / "cluster.json"
    state.write_text(json.dumps({"resources": {}, "version": 0}))
    executable = tmp_path / "kubectl"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import copy,json,os,sys
from pathlib import Path
path=Path(os.environ['UPDATE_TEST_CLUSTER']);cluster=json.loads(path.read_text())
args=sys.argv[1:]
if args[:2]==['--namespace','default']:args=args[2:]
resources=cluster['resources']
def saved(obj):
    cluster['version']+=1
    obj.setdefault('metadata',{}).update(resourceVersion=str(cluster['version']))
    obj['metadata'].setdefault('uid',obj['kind'].lower()+'-uid')
    obj['metadata'].setdefault('generation',1)
    resources[obj['kind'].lower()]=obj
    return obj
def reconcile():
    store=resources.get('secretstore');parent=resources.get('externalsecret')
    for obj in (store,parent):
        if obj:obj['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':obj['metadata']['generation']}]}
    if parent:parent['status']['syncedResourceVersion']=str(parent['metadata']['generation'])+'-fixture'
    if not store or not parent:return
    if cluster.get('never_converge') and store['metadata'].get('labels',{}).get('sieve.testing/state')=='D':return
    version=parent['spec']['data'][0]['remoteRef'].get('version','v1')
    value=next(d['value'] for d in store['spec']['provider']['fake']['data'] if d.get('version')==version)
    import base64
    secret=resources.get('secret') or {'apiVersion':'v1','kind':'Secret','metadata':{'name':parent['metadata']['name'],'namespace':'default','labels':{},'ownerReferences':[{'uid':parent['metadata']['uid'],'kind':'ExternalSecret','name':parent['metadata']['name'],'controller':True}]}}
    secret['data']={'marker':base64.b64encode(value.encode()).decode()}
    saved(secret)
if args[0]=='apply':
    result=saved(json.load(sys.stdin));reconcile()
elif args[0]=='get':
    result=resources.get(args[1].lower())
elif args[0]=='patch':
    obj=resources[args[1].lower()];before=copy.deepcopy(obj)
    for change in json.loads(args[args.index('-p')+1]):
        parts=[p.replace('~1','/').replace('~0','~') for p in change['path'].split('/')[1:]]
        parent=obj
        for part in parts[:-1]:parent=parent[int(part)] if isinstance(parent,list) else parent[part]
        key=int(parts[-1]) if isinstance(parent,list) else parts[-1]
        if change['op']=='test':assert parent[key]==change['value']
        elif cluster.get('reject_updates') and change['path'].endswith('sieve.testing~1state'):pass
        else:parent[key]=change['value']
    if obj.get('spec')!=before.get('spec'):obj['metadata']['generation']+=1
    result=copy.deepcopy(saved(obj));reconcile()
else:raise SystemExit('unsupported command')
path.write_text(json.dumps(cluster))
if result is not None:print(json.dumps(result))
"""
    )
    executable.chmod(0o755)
    return {
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
        "UPDATE_TEST_CLUSTER": str(state),
    }


def test_store_updates_record_independent_accepted_states(tmp_path, update_cluster):
    output = tmp_path / "result/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/external-secrets/workloads/workload.py"),
            "miss-store-updates",
            str(output),
            "--update-interval",
            "0.02",
            "--repair-timeout",
            "2",
            "--settle-seconds",
            "0",
        ],
        env=update_cluster,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    events = json.loads(output.with_name("workload-updates.json").read_text())
    assert events["status"] == "completed"
    assert events["resource_key"] == "secretstore/default/sieve-local-store"
    assert [
        e["state"]["metadata"]["labels"]["sieve.testing/state"]
        for e in events["updates"]
    ] == ["A", "B", "C", "D"]
    assert all(
        e["producer"] == "workload"
        and e["response"]["metadata"]["uid"] == "secretstore-uid"
        for e in events["updates"]
    )
    assert (
        len({e["response"]["metadata"]["resourceVersion"] for e in events["updates"]})
        == 4
    )
    evidence = json.loads(output.with_name("update-observations.json").read_text())
    assert evidence["status"] == "converged"
    assert evidence["parent_before"]["spec"] == evidence["parent_after"]["spec"]
    assert evidence["child_after"]["data"]["marker"] == "ZmluYWwtdmFsdWU="


def test_child_updates_keep_the_parent_unchanged(tmp_path, update_cluster):
    output = tmp_path / "child/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/external-secrets/workloads/workload.py"),
            "miss-child-updates",
            str(output),
            "--update-interval",
            "0.02",
            "--repair-timeout",
            "2",
            "--settle-seconds",
            "0",
        ],
        env=update_cluster,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source = json.loads(output.with_name("workload-updates.json").read_text())
    assert source["resource_key"] == "secret/default/sieve-external-secret"
    assert [
        u["state"]["metadata"]["labels"]["sieve.testing/state"]
        for u in source["updates"]
    ] == ["A", "B", "C", "D"]
    evidence = json.loads(output.with_name("update-observations.json").read_text())
    assert evidence["status"] == "converged"
    assert evidence["parent_before"]["spec"] == evidence["parent_after"]["spec"]
    assert (
        evidence["parent_before"]["metadata"]["generation"]
        == evidence["parent_after"]["metadata"]["generation"]
    )
    assert evidence["child_after"]["data"]["marker"] == "ZXhwZWN0ZWQtdmFsdWU="
    assert evidence["child_after"]["metadata"]["labels"]["sieve.testing/state"] == "D"


def test_parent_updates_converge_to_the_final_version(tmp_path, update_cluster):
    output = tmp_path / "parent/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/external-secrets/workloads/workload.py"),
            "miss-parent-updates",
            str(output),
            "--update-interval",
            "0.02",
            "--repair-timeout",
            "2",
            "--settle-seconds",
            "0",
        ],
        env=update_cluster,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    source = json.loads(output.with_name("workload-updates.json").read_text())
    assert source["resource_key"] == "externalsecret/default/sieve-external-secret"
    assert [
        u["response"]["spec"]["data"][0]["remoteRef"]["version"]
        for u in source["updates"]
    ] == ["v1", "v2", "v3", "v4"]
    evidence = json.loads(output.with_name("update-observations.json").read_text())
    assert evidence["status"] == "converged"
    assert (
        evidence["parent_before"]["metadata"]["uid"]
        == evidence["parent_after"]["metadata"]["uid"]
    )
    assert evidence["parent_after"]["metadata"]["generation"] == 4
    assert evidence["child_after"]["data"]["marker"] == "ZmluYWwtdmFsdWU="


@pytest.mark.parametrize("failure", ["reject_updates", "never_converge"])
def test_failed_updates_cannot_produce_a_successful_source(
    tmp_path, update_cluster, failure
):
    cluster = Path(update_cluster["UPDATE_TEST_CLUSTER"])
    state = json.loads(cluster.read_text())
    state[failure] = True
    cluster.write_text(json.dumps(state))
    output = tmp_path / "failed/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/external-secrets/workloads/workload.py"),
            "miss-store-updates",
            str(output),
            "--update-interval",
            "0.02",
            "--repair-timeout",
            "1",
            "--settle-seconds",
            "0",
        ],
        env=update_cluster,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    source = json.loads(output.with_name("workload-updates.json").read_text())
    assert source["status"] == "failed"
    if failure == "reject_updates":
        assert source["updates"] == []
    else:
        assert len(source["updates"]) == 4
