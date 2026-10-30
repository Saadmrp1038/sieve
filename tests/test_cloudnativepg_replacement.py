"""Pooler identity replacement through the public workload/Kubernetes boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def replacement_cluster(tmp_path):
    state = tmp_path / "cluster.json"
    command = tmp_path / "kubectl"
    command.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json,os,sys
from pathlib import Path
path=Path(os.environ['CNPG_REPLACEMENT_CLUSTER']);s=json.loads(path.read_text());a=sys.argv[1:]
if a[:2]==['--namespace','default']:a=a[2:]
verb=a[0]
def save():path.write_text(json.dumps(s))
def emit(obj):save();print(json.dumps(obj) if obj is not None else '')
def resource(kind,name,uid,spec):return {'apiVersion':'postgresql.cnpg.io/v1' if kind in ('Pooler','Cluster') else 'v1','kind':kind,'metadata':{'name':name,'namespace':'default','uid':uid,'generation':1,'resourceVersion':str(s.get('version',1))},'spec':spec}
def child(kind):
 p=s.get('pooler')
 if not p:return None
 old=s.get(kind)
 if s.get('scenario')=='old-owner' and old:return old
 obj=resource('Deployment' if kind=='deployment' else 'Service','sieve-pooler',kind+'-'+p['metadata']['uid'],{'replicas':p['spec']['instances']} if kind=='deployment' else {})
 obj['metadata']['ownerReferences']=[{'uid':p['metadata']['uid'],'kind':'Pooler','name':'sieve-pooler','controller':True}]
 obj['status']={'readyReplicas':p['spec']['instances'],'replicas':p['spec']['instances'],'observedGeneration':1}
 s[kind]=obj;return obj
if verb in ('apply','create'):
 obj=json.load(sys.stdin);k=obj['kind'].lower();s['version']=s.get('version',0)+1
 uid=k+'-'+str(s['version']);r=resource(obj['kind'],obj['metadata']['name'],uid,obj['spec']);r['metadata'].update(obj['metadata'])
 if k=='cluster':r['status']={'readyInstances':3,'currentPrimary':'sieve-db-1','targetPrimary':'sieve-db-1'}
 s[k]=r;emit(r)
elif verb=='get':
 k=a[1].split('.')[0].rstrip('s')
 if k in ('deployment','service'):emit(child(k))
 elif k=='pod':
  items=[]
  for i in range(1,4):
   obj=resource('Pod','sieve-db-'+str(i),'pod-'+str(i),{});obj['metadata']['ownerReferences']=[{'uid':s['cluster']['metadata']['uid'],'kind':'Cluster','controller':True}];obj['status']={'conditions':[{'type':'Ready','status':'True'}]};items.append(obj)
  emit({'items':items})
 else:emit(s.get(k))
elif verb=='patch':
 k=a[1].split('.')[0].rstrip('s');obj=s[k];operations=json.loads(a[a.index('-p')+1])
 for op in operations:
  parts=op['path'].strip('/').split('/');parent=obj
  for part in parts[:-1]:parent=parent.setdefault(part,{})
  if op['op']=='test':
   if parent.get(parts[-1])!=op['value']:raise SystemExit('UID precondition failed')
  else:parent[parts[-1]]=op['value']
 s['version']+=1;obj['metadata']['resourceVersion']=str(s['version']);emit(obj)
elif verb=='delete':
 body=json.load(sys.stdin);obj=s['pooler']
 if body['preconditions']['uid']!=obj['metadata']['uid']:raise SystemExit('UID precondition failed')
 s.setdefault('deleted',[]).append(obj['metadata']['uid']);s['pooler']=None
 emit({'kind':'Status','status':'Success','details':{'uid':obj['metadata']['uid']}})
elif verb=='exec':
 statement=a[-1]
 if 'pg_is_in_recovery' in statement:print('f' if a[1]=='sieve-db-1' else 't')
 elif 'json_agg' in statement:print(json.dumps([[i,'ack-'+str(i)] for i in range(1,32 if s.get('scenario')=='lost-data' and s.get('deleted') else 33)]))
 else:print('')
 save()
else:raise SystemExit('Unsupported operation')
"""
    )
    command.chmod(0o755)
    return state, {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CNPG_REPLACEMENT_CLUSTER": str(state),
    }


def run_workload(tmp_path, environment):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-replacement",
            str(tmp_path / "workload.log"),
            "--ready-timeout",
            "2",
            "--poll-interval",
            "0.01",
            "--replacement-delay",
            "0.01",
            "--observe-seconds",
            "0.05",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_pooler_replacement_requires_new_identity_owned_children_and_database_data(
    tmp_path, replacement_cluster
):
    state, environment = replacement_cluster
    state.write_text(json.dumps({"scenario": "healthy"}))
    result = run_workload(tmp_path, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "replacement-observations.json").read_text())
    assert (
        evidence["old_pooler"]["metadata"]["uid"]
        != evidence["new_pooler"]["metadata"]["uid"]
    )
    assert (
        evidence["delete_response"]["details"]["uid"]
        == evidence["old_pooler"]["metadata"]["uid"]
    )
    assert evidence["new_pooler"]["spec"]["instances"] == 2
    assert len(evidence["database_rows"]) == 3
    checks = json.loads((tmp_path / "semantic-outcome.json").read_text())["checks"]
    assert checks and all(check["passed"] for check in checks)


@pytest.mark.parametrize(
    "scenario,failed_check",
    [
        ("old-owner", "replacement_owns_children"),
        ("lost-data", "acknowledged_sql_rows_preserved"),
    ],
)
def test_replacement_rejects_stale_ownership_and_missing_acknowledged_rows(
    tmp_path, replacement_cluster, scenario, failed_check
):
    state, environment = replacement_cluster
    state.write_text(json.dumps({"scenario": scenario}))
    result = run_workload(tmp_path, environment)
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "replacement-observations.json").read_text())
    assert evidence["status"] == "semantic_failure"
    checks = json.loads((tmp_path / "semantic-outcome.json").read_text())["checks"]
    assert {c["name"]: c["passed"] for c in checks}[failed_check] is False
    assert "FINISH-SIEVE-TEST" not in (tmp_path / "workload.log").read_text()
