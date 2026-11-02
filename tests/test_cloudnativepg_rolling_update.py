"""Rolling updates through the workload CLI and external Kubernetes commands."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def rolling_boundary(tmp_path):
    state = tmp_path / "api.json"
    state.write_text("{}")
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json,os,sys,time
from pathlib import Path
p=Path(os.environ['ROLLING_API']);s=json.loads(p.read_text());a=sys.argv[1:]
if a[:2]==['--namespace','default']:a=a[2:]
def emit(x):
 p.write_text(json.dumps(s));print(json.dumps(x))
def pods():
 return [{'kind':'Pod','metadata':{'name':'db-'+str(i),'uid':'pod-'+str(i),'ownerReferences':[{'kind':'Cluster','name':'sieve-rolling','uid':'cluster-uid','controller':True}]},'status':{'conditions':[{'type':'Ready','status':'True'}]}} for i in range(1,4)]
if a[0]=='apply':
 r=json.load(sys.stdin);r['metadata'].update(uid='cluster-uid',generation=1,resourceVersion='1');s['cluster']=r;emit(r)
elif a[0]=='wait':print('ready')
elif a[0]=='create':
 r=json.load(sys.stdin);s['client_start']=time.time_ns();emit(r)
elif a[0]=='patch':
 r=s['cluster'];ops=json.loads(a[a.index('-p')+1]);assert ops[0]['op']=='test' and ops[0]['value']=='cluster-uid'
 for op in ops[1:]:
  if op['path']=='/spec/postgresql/parameters/max_connections':r['spec']['postgresql']['parameters']['max_connections']=op['value']
  elif op['path']=='/metadata/annotations':r['metadata']['annotations']=op['value']
 r['metadata']['generation']+=1;r['metadata']['resourceVersion']=str(r['metadata']['generation']);emit(r)
elif a[0]=='get':
 if a[1].startswith('clusters'):
  r=s['cluster'];r['status']={'readyInstances':3,'currentPrimary':'db-1','targetPrimary':'db-1','phase':'Cluster in healthy state'};emit(r)
 elif a[1]=='pods':emit({'items':pods()})
 elif a[1]=='pod':emit({'status':{'containerStatuses':[{'state':{'terminated':{'exitCode':0}}}]*2}})
 else:raise SystemExit(repr(a))
elif a[0]=='exec':
 q=a[-1]
 if 'json_build_object' in q:
  setting=s['cluster']['spec']['postgresql']['parameters']['max_connections']
  emit({'max_connections':setting,'pending_restart':False,'recovery':a[1]!='db-1','postmaster_start':'initial' if setting=='100' else 'final','synchronous_commit':'remote_apply','synchronous_standby_names':'ANY 1 (db_2,db_3)','sync_standbys':1,'rows':[[i,'ack-'+str(i)] for i in (1,2,3,4,5,6) if not(s.get('scenario')=='lost-ack' and i==1)] if s.get('stopped') else []})
 elif 'touch' in a:s['stopped']=True;p.write_text(json.dumps(s))
 else:print('')
elif a[0]=='logs':
 stream=a[a.index('-c')+1];start=s['client_start'];finish=time.time_ns();records=[]
 for i in range(1,7):
  ok=i!=2;ts=start+i if i<4 else finish-100+ i
  records.append({'kind':'attempt','stream':stream,'seq':i,'started_ns':ts,'ended_ns':ts+1,'monotonic_started_ns':ts,'monotonic_ended_ns':ts+1,'returncode':0 if ok else 1,'timed_out':False,'timeout_seconds':5,'stdout':('ACK:'+str(i)) if stream=='write' and ok else '{}','stderr':'','ok':ok,'acknowledged':ok and stream=='write','outcome':'ambiguous' if not ok and stream=='write' else 'success'})
 if s.get('scenario')=='bad-read' and stream=='read' and s.get('stopped'):records[-1].update(ok=False,returncode=0)
 if s.get('stopped'):records.append({'kind':'terminal','stream':stream,'attempts':6,'reason':'stop','ended_ns':finish})
 print('\n'.join(json.dumps(r) for r in records))
else:raise SystemExit(repr(a))
"""
    )
    kubectl.chmod(0o755)
    return state, {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "ROLLING_API": str(state),
    }


def invoke(tmp_path, boundary):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "rolling-config-update",
            str(tmp_path / "workload.log"),
            "--ready-timeout",
            "3",
            "--observe-seconds",
            "0.03",
            "--poll-interval",
            "0.01",
            "--update-interval",
            "0.02",
            "--warmup-seconds",
            "0.03",
        ],
        env=boundary[1],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_rolling_update_verifies_latest_config_and_only_acknowledged_rows(
    tmp_path, rolling_boundary
):
    result = invoke(tmp_path, rolling_boundary)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "rolling-observations.json").read_text())
    assert [
        u["response"]["spec"]["postgresql"]["parameters"]["max_connections"]
        for u in evidence["updates"]
    ] == ["120", "140"]
    assert evidence["acknowledged_ids"] == [1, 3, 4, 5, 6]
    assert evidence["ambiguous_ids"] == [2]
    assert len(evidence["final_sql"]) == 3
    assert all(r["max_connections"] == "140" for r in evidence["final_sql"].values())
    assert (tmp_path / "client-write.jsonl").exists()


def test_rolling_update_rejects_missing_acknowledged_data(tmp_path, rolling_boundary):
    rolling_boundary[0].write_text(json.dumps({"scenario": "lost-ack"}))
    result = invoke(tmp_path, rolling_boundary)
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "rolling-observations.json").read_text())
    assert evidence["status"] == "semantic_failure"
    assert all(ids == [1] for ids in evidence["missing_acknowledged_ids"].values())


def test_rolling_update_rejects_successful_reads_with_bad_data(
    tmp_path, rolling_boundary
):
    rolling_boundary[0].write_text(json.dumps({"scenario": "bad-read"}))
    result = invoke(tmp_path, rolling_boundary)
    assert result.returncode == 1
    checks = json.loads((tmp_path / "semantic-outcome.json").read_text())["checks"]
    assert {c["name"]: c["passed"] for c in checks}[
        "no_successful_reads_with_bad_data"
    ] is False
