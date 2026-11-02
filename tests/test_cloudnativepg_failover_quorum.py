"""The public workload rejects unsafe elections and restores isolated workers."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def quorum_boundary(tmp_path):
    state = tmp_path / "boundary.json"
    state.write_text("{}")
    script = r"""
import json,os,sys,time
from pathlib import Path
p=Path(os.environ['QUORUM_BOUNDARY']);s=json.loads(p.read_text());a=sys.argv[1:]
tool=Path(sys.argv[0]).name
def emit(x):
 p.write_text(json.dumps(s));print(json.dumps(x))
def pod(i):
 return {'kind':'Pod','metadata':{'name':'sieve-quorum-'+str(i),'uid':'pod-'+str(i),'ownerReferences':[{'kind':'Cluster','name':'sieve-quorum','uid':'cluster-uid','controller':True}]},'spec':{'nodeName':'kind-worker'+str(i)},'status':{'conditions':[{'type':'Ready','status':'False' if 'kind-worker'+str(i) in s.get('isolated',[]) else 'True'}]}}
def unsafe():return s.get('scenario')=='unsafe' and s.get('restored_A') and len(s.get('isolated',[]))==2
if tool=='docker':
 if a[0]=='inspect':
  if 'NetworkSettings' in a[2]:emit({} if a[-1] in s.get('isolated',[]) else {'kind':{'IPAddress':'172.18.0.'+a[-1][-1],'GlobalIPv6Address':''}})
  else:emit({'io.x-k8s.kind.cluster':'kind','io.x-k8s.kind.role':'worker'})
 elif a[:2]==['network','disconnect']:
  s.setdefault('isolated',[]).append(a[-1])
  if s.get('scenario')=='disconnect-error' and len(s['isolated'])==3:
   p.write_text(json.dumps(s));print('disconnect response lost',file=sys.stderr);raise SystemExit(1)
  emit('disconnected')
 elif a[:2]==['network','connect']:
  s['isolated'].remove(a[-1]);s.setdefault('restored',[]).append(a[-1])
  if a[-1]=='kind-worker2':s['restored_A']=True
  emit('connected')
 else:raise SystemExit(repr(a))
else:
 if a[:2]==['--namespace','default']:a=a[2:]
 if a[0]=='apply':
  r=json.load(sys.stdin);r['metadata'].update(uid='cluster-uid',generation=1,resourceVersion='1');s['cluster']=r;emit(r)
 elif a[0]=='patch':
  r=s['cluster'];ops=json.loads(a[a.index('-p')+1]);assert ops[0]['value']=='cluster-uid'
  for op in ops[1:]:
   if op['path']=='/metadata/annotations':r['metadata']['annotations']=op['value']
   elif op['path']=='/spec/postgresql/synchronous/failoverQuorum':r['spec']['postgresql']['synchronous']['failoverQuorum']=op['value']
   elif op['path']=='/spec/postgresql/synchronous/number':r['spec']['postgresql']['synchronous']['number']=op['value']
  r['metadata']['resourceVersion']=str(int(r['metadata']['resourceVersion'])+1);r['metadata']['generation']+=1;emit(r)
 elif a[0]=='label':print('labelled')
 elif a[0]=='get':
  if a[1].startswith('clusters'):
   r=s['cluster'];r['status']={'readyInstances':3-len(s.get('isolated',[])),'currentPrimary':'sieve-quorum-2' if unsafe() else 'sieve-quorum-1','targetPrimary':'sieve-quorum-2' if unsafe() else 'sieve-quorum-1'};emit(r)
  elif a[1].startswith('failoverquorums'):
   emit({'kind':'FailoverQuorum','metadata':{'uid':'quorum-uid','resourceVersion':s['cluster']['metadata']['resourceVersion'],'name':'sieve-quorum'},'status':{'primary':'sieve-quorum-1','method':'ANY','standbyNumber':s['cluster']['spec']['postgresql']['synchronous']['number'],'standbyNames':['sieve-quorum-2','sieve-quorum-3']}})
  elif a[1]=='nodes':emit({'items':[{'metadata':{'name':'kind-worker'+str(i)}} for i in range(1,4)]})
  elif a[1]=='pods':emit({'items':[pod(i) for i in range(1,4)]})
  elif a[1]=='pod':emit({'status':{'containerStatuses':[{'state':{'terminated':{'exitCode':0}}}]*2}})
  else:raise SystemExit(repr(a))
 elif a[0]=='exec':
  if a[1] in ('sieve-quorum-1','sieve-quorum-2','sieve-quorum-3'):
   if 'kind-worker'+a[1][-1] in s.get('isolated',[]):print('unreachable',file=sys.stderr);raise SystemExit(1)
  q=a[-1]
  if 'json_build_object' in q:
   i=int(a[1][-1]);rows=[[j,'ack-'+str(j)] for j in range(1,7 if s.get('stopped') else 4)] if s.get('client_start') else []
   if s.get('witness') and (i!=2 or not s.get('isolated')):rows.append([1000000000,'ack-1000000000'])
   if s.get('scenario')=='lost-ack' and s.get('stopped'):rows=[r for r in rows if r[0]!=1]
   emit({'recovery':i!=(2 if unsafe() else 1),'synchronous_commit':'remote_apply','synchronous_standby_names':'ANY '+str(s['cluster']['spec']['postgresql']['synchronous']['number'])+' ("sieve-quorum-2","sieve-quorum-3")','sync_standbys':2 if s['cluster']['spec']['postgresql']['synchronous']['number']==2 else 1,'rows':rows,'receive_lsn':'0/100','replay_lsn':'0/100','timeline':1})
  elif 'ACK:1000000000' in q:
   s['witness']=True;p.write_text(json.dumps(s));print('ACK:1000000000')
  elif 'touch' in a:s['stopped']=True;p.write_text(json.dumps(s))
  else:print('')
 elif a[0]=='create':
  r=json.load(sys.stdin);s['client_start']=time.time_ns();emit(r)
 elif a[0]=='wait':print('ready')
 elif a[0]=='logs':
  stream=a[a.index('-c')+1];t=s['client_start'];n=time.time_ns();records=[]
  for i in range(1,7 if s.get('stopped') else 4):
   ts=t+i if i<=3 else n-20+i
   records.append({'kind':'attempt','stream':stream,'seq':i,'started_ns':ts,'ended_ns':ts+1,'monotonic_started_ns':ts,'monotonic_ended_ns':ts+1,'returncode':0,'timed_out':False,'timeout_seconds':5,'stdout':'ACK:'+str(i) if stream=='write' else '{"count":3,"max":3,"bad":0}','stderr':'','ok':True,'acknowledged':stream=='write','outcome':'acknowledged' if stream=='write' else 'success'})
  if s.get('stopped'):records.append({'kind':'terminal','stream':stream,'attempts':6,'reason':'stop','ended_ns':n})
  print('\n'.join(json.dumps(r) for r in records))
 else:raise SystemExit(repr(a))
"""
    for name in ("kubectl", "docker"):
        command = tmp_path / name
        command.write_text(f"#!{sys.executable}\n" + script)
        command.chmod(0o755)
    return state, {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "QUORUM_BOUNDARY": str(state),
    }


def invoke(tmp_path, boundary):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "failover-quorum-update",
            str(tmp_path / "workload.log"),
            "--ready-timeout",
            "3",
            "--observe-seconds",
            "0.03",
            "--poll-interval",
            "0.01",
            "--warmup-seconds",
            "0.03",
            "--stable-seconds",
            "0.03",
        ],
        cwd=ROOT,
        env=boundary[1],
        capture_output=True,
        text=True,
        timeout=25,
    )


def test_quorum_workload_preserves_acknowledgements_and_restores_workers(
    tmp_path, quorum_boundary
):
    result = invoke(tmp_path, quorum_boundary)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "quorum-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert evidence["witness"]["acknowledged"] is True
    assert evidence["single_replica_samples"]
    assert all(r["sql"]["recovery"] for r in evidence["single_replica_samples"])
    assert len(evidence["final_sql"]) == 3
    assert json.loads(quorum_boundary[0].read_text())["isolated"] == []


def test_quorum_workload_rejects_promotion_of_the_stale_survivor(
    tmp_path, quorum_boundary
):
    quorum_boundary[0].write_text(json.dumps({"scenario": "unsafe"}))
    result = invoke(tmp_path, quorum_boundary)
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "quorum-observations.json").read_text())
    assert evidence["status"] == "semantic_failure"
    checks = json.loads((tmp_path / "semantic-outcome.json").read_text())["checks"]
    assert {c["name"]: c["passed"] for c in checks}[
        "no_automatic_failover_without_current_quorum"
    ] is False
    assert json.loads(quorum_boundary[0].read_text())["isolated"] == []


def test_quorum_workload_rejects_missing_acknowledged_numbered_write(
    tmp_path, quorum_boundary
):
    quorum_boundary[0].write_text(json.dumps({"scenario": "lost-ack"}))
    result = invoke(tmp_path, quorum_boundary)
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "quorum-observations.json").read_text())
    assert evidence["status"] == "semantic_failure"
    assert all(ids == [1] for ids in evidence["missing_acknowledged_ids"].values())


def test_quorum_workload_restores_every_worker_after_ambiguous_disconnect(
    tmp_path, quorum_boundary
):
    quorum_boundary[0].write_text(json.dumps({"scenario": "disconnect-error"}))
    result = invoke(tmp_path, quorum_boundary)
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "quorum-observations.json").read_text())
    assert evidence["status"] == "incomplete"
    assert evidence["network_restore_errors"] == []
    assert json.loads(quorum_boundary[0].read_text())["isolated"] == []
