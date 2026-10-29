"""CloudNativePG benchmark through its workload CLI and Kubernetes boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sieve_tool.common.config import load_controller_config

ROOT = Path(__file__).resolve().parents[1]


def test_profile_pins_native_graph_and_unchanged_default_concurrency():
    config = load_controller_config(ROOT / "controllers/cloudnative-pg")
    assert config.commit == "9bb80d2292f5845ac6c2d60dc691b222a5ac4655"
    assert config.client_go_version == "v0.37.0"
    assert (
        config.test_setting["pooler-finalizer-updates"]["independent_updates"] is True
    )
    assert (ROOT / config.test_command[1]).is_file()
    deployment = (ROOT / "controllers/cloudnative-pg/deploy/operator.yaml").read_text()
    assert "--max-concurrent-reconciles=10" in deployment
    assert "name: sieveTestPlan" in deployment
    container = yaml.safe_load(deployment)["spec"]["template"]["spec"]["containers"][0]
    operand_image = next(
        e["value"] for e in container["env"] if e["name"] == "OPERATOR_IMAGE_NAME"
    )
    assert operand_image == "${SIEVE-DR}/cloudnative-pg:vanilla"


def test_postgresql_digest_requires_the_version_tag_before_kubernetes_setup(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "fixed"}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-finalizer-updates",
            str(tmp_path / "workload.log"),
            "--postgres-image",
            "ghcr.io/cloudnative-pg/postgresql@sha256:" + "1" * 64,
            "--ready-timeout",
            "0.01",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "version tag" in result.stderr
    assert json.loads(state.read_text()) == {"scenario": "fixed"}


@pytest.fixture
def kubernetes_boundary(tmp_path):
    state = tmp_path / "cluster.json"
    command = tmp_path / "kubectl"
    command.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys, time
from pathlib import Path
path=Path(os.environ['CNPG_TEST_CLUSTER']); s=json.loads(path.read_text())
a=sys.argv[1:]
if a[:2]==['--namespace','default']:a=a[2:]
verb=a[0]
def save():
 temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(s));temporary.replace(path)
def emit(v):
 save(); print(json.dumps(v) if v is not None else '')
def resource(kind,name,uid):
 return {'apiVersion':'v1','kind':kind,'metadata':{'name':name,'namespace':'default','uid':uid,'generation':1,'resourceVersion':'1'}}
def child(kind):
 p=s.get('pooler')
 if p and (not p['metadata'].get('deletionTimestamp') or s['scenario']=='buggy') and s.get(kind) is None:
  s['counter']=s.get('counter',0)+1
  r=resource('Deployment' if kind=='deployment' else 'Service','sieve-pooler',kind+str(s['counter']))
  r['metadata']['ownerReferences']=[{'uid':p['metadata']['uid'],'kind':'Pooler','name':'sieve-pooler','controller':True}]
  r['status']={'readyReplicas':1,'availableReplicas':1}
  s[kind]=r
 return s.get(kind)
if verb=='apply':
 r=json.load(sys.stdin); k='cluster' if r['kind']=='Cluster' else 'pooler'
 r['metadata'].update(uid=k+'-uid',generation=1,resourceVersion='1')
 r['status']={'readyInstances':3,'instances':3,'currentPrimary':'sieve-db-1','targetPrimary':'sieve-db-1','conditions':[{'type':'Ready','status':'True'}]}
 s[k]=r; emit(r)
elif verb=='get':
 k=a[1].split('.')[0].rstrip('s')
 if '--watch' in a:
  previous=None; transient=False
  def event(action,uid):
   kind='Deployment' if k=='deployment' else 'Service'
   if a[-1]=='json':
    print(json.dumps({'type':action,'object':{'kind':kind,'metadata':{'uid':uid,'ownerReferences':[{'uid':'pooler-uid'}]}}},indent=2),flush=True)
   else:
    # kubectl's text tabwriter expands JSONPath tabs during a watch.
    print('   '.join([action,kind,uid,'pooler-uid']),flush=True)
  while True:
   try:current=json.loads(path.read_text())
   except json.JSONDecodeError:time.sleep(.001);continue
   obj=current.get(k);uid=obj['metadata']['uid'] if obj else None
   if uid!=previous and not (current['scenario']=='stalled' and previous is not None):
    if previous:event('DELETED',previous)
    if uid:event('ADDED',uid)
    previous=uid
   if current['scenario']=='transient' and not transient and current.get('pooler',{}).get('metadata',{}).get('annotations',{}).get('sieve.testing/phase') in ('C','D'):
    transient=True
    event('ADDED','transient-'+k)
    event('DELETED','transient-'+k)
   time.sleep(.001)
 if k=='pod':
  items=[]
  for i in range(1,4):
   r=resource('Pod','sieve-db-'+str(i),'pod-'+str(i));r['metadata']['ownerReferences']=[{'uid':'cluster-uid','kind':'Cluster','controller':True}];r['status']={'conditions':[{'type':'Ready','status':'True'}]};items.append(r)
  emit({'items':items})
 elif k in ('deployment','service'):emit(child(k))
 else:emit(s.get(k))
elif verb=='patch':
 k=a[1].split('.')[0].rstrip('s'); r=s[k]; operations=json.loads(a[a.index('-p')+1])
 for op in operations:
  parts=[v.replace('~1','/').replace('~0','~') for v in op['path'].strip('/').split('/')]; parent=r
  for v in parts[:-1]:parent=parent.setdefault(v,{})
  if op['op']=='test':
   if parent.get(parts[-1])!=op['value']:raise SystemExit('UID precondition failed')
  else:parent[parts[-1]]=op['value']
 r['metadata']['resourceVersion']=str(int(r['metadata']['resourceVersion'])+1);emit(r)
elif verb=='delete':
 raw=a[a.index('--raw')+1]; k=raw.split('/')[-2].rstrip('s'); body=json.load(sys.stdin); r=s[k]
 if r['metadata']['uid']!=body['preconditions']['uid']:raise SystemExit('UID precondition failed')
 uid=r['metadata']['uid'];s.setdefault('deletions',[]).append({'kind':k,'uid':uid})
 if k=='pooler':r['metadata']['deletionTimestamp']='2026-10-28T18:00:00Z'
 else:s[k]=None
 emit({'kind':'Status','status':'Success','details':{'uid':uid}})
elif verb=='exec':
 sql=a[-1]
 if 'pg_is_in_recovery' in sql:print('f' if a[1]=='sieve-db-1' else 't')
 elif 'json_agg' in sql:
  rows=[[i,'ack-'+str(i)] for i in range(1,33)]
  if s['scenario']=='lost-data' and s.get('pooler',{}).get('metadata',{}).get('deletionTimestamp'):rows=rows[:-1]
  print(json.dumps(rows))
 else:print('')
 save()
else:raise SystemExit('unsupported kubectl operation '+repr(a))
"""
    )
    command.chmod(0o755)
    return state, {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CNPG_TEST_CLUSTER": str(state),
    }


@pytest.mark.parametrize(
    "scenario, expected",
    [("fixed", 0), ("buggy", 1), ("lost-data", 1), ("transient", 1), ("stalled", 1)],
)
def test_workload_detects_recreated_children_and_lost_sql_rows(
    tmp_path, kubernetes_boundary, scenario, expected
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": scenario}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-finalizer-updates",
            str(tmp_path / "workload.log"),
            "--update-interval",
            "0.001",
            "--observe-seconds",
            "0.05",
            "--poll-interval",
            "0.01",
            "--ready-timeout",
            "2",
            "--postgres-image",
            "ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie@sha256:1111111111111111111111111111111111111111111111111111111111111111",
            "--pgbouncer-image",
            "ghcr.io/cloudnative-pg/pgbouncer:1.25.1@sha256:2222222222222222222222222222222222222222222222222222222222222222",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    if scenario == "stalled":
        evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
        assert evidence["status"] == "harness_or_setup_failure"
        assert "watch did not acknowledge" in evidence["error"]
        assert not (tmp_path / "semantic-outcome.json").exists()
        return
    semantic = json.loads((tmp_path / "semantic-outcome.json").read_text())
    checks = {c["name"]: c["passed"] for c in semantic["checks"]}
    assert checks["no_children_recreated_after_owner_deletion"] is (
        scenario not in ("buggy", "transient")
    )
    assert checks["acknowledged_sql_rows_preserved"] is (scenario != "lost-data")
    assert (
        len(json.loads((tmp_path / "workload-updates.json").read_text())["updates"])
        == 4
    )
    assert all(d["uid"] for d in json.loads(state.read_text())["deletions"])
    assert "@sha256:" in json.loads(state.read_text())["cluster"]["spec"]["imageName"]
