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
 if s['scenario']=='interrupt-after-delete' and s.get('deletions') and k=='deployment':
  os.kill(os.getppid(),9);raise SystemExit(0)
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
 elif k in ('deployment','service'):
  if k=='service' and s['scenario']=='missing-service':emit(None)
  elif k=='service' and s['scenario']=='slow-service':
   time.sleep(3);emit(child(k))
  elif k=='service' and s['scenario']=='foreign-service':
   r=child(k);r['metadata']['ownerReferences'][0]['uid']='foreign-pooler';emit(r)
  elif k=='service' and s['scenario']=='delayed-service' and s.get('service_reads',0)<3:
   s['service_reads']=s.get('service_reads',0)+1;emit(None)
  else:emit(child(k))
 else:emit(s.get(k))
elif verb=='patch':
 k=a[1].split('.')[0].rstrip('s'); r=s[k]; operations=json.loads(a[a.index('-p')+1])
 phase=next((o['value'].get('sieve.testing/phase') for o in operations if o['path']=='/metadata/annotations'),None)
 if s['scenario'] in ('webhook-restarts','webhook-unavailable','webhook-denied') and phase=='B' and (s['scenario']!='webhook-restarts' or not s.get('webhook_refusals')):
  s['webhook_refusals']=s.get('webhook_refusals',0)+1;save()
  message='Error from server (Forbidden): pooler metadata update denied by policy' if s['scenario']=='webhook-denied' else 'Error from server (InternalError): Internal error occurred: failed calling webhook "vpooler.cnpg.io": failed to call webhook: Post "https://cnpg-webhook-service.default.svc:443/validate-postgresql-cnpg-io-v1-pooler?timeout=10s": dial tcp 10.96.0.1:443: connect: connection refused'
  print(message,file=sys.stderr)
  raise SystemExit(1)
 for op in operations:
  parts=[v.replace('~1','/').replace('~0','~') for v in op['path'].strip('/').split('/')]; parent=r
  for v in parts[:-1]:parent=parent.setdefault(v,{})
  if op['op']=='test':
   if parent.get(parts[-1])!=op['value']:raise SystemExit('UID precondition failed')
  else:parent[parts[-1]]=op['value']
 if s['scenario']=='unexpected-generation' and r['metadata'].get('annotations',{}).get('sieve.testing/phase')=='C':r['metadata']['generation']+=1
 r['metadata']['resourceVersion']=str(int(r['metadata']['resourceVersion'])+1);emit(r)
elif verb=='delete':
 raw=a[a.index('--raw')+1]; k=raw.split('/')[-2].rstrip('s'); body=json.load(sys.stdin); r=s[k]
 if r['metadata']['uid']!=body['preconditions']['uid']:raise SystemExit('UID precondition failed')
 uid=r['metadata']['uid'];s.setdefault('deletions',[]).append({'kind':k,'uid':uid})
 if k=='pooler':
  r['metadata']['deletionTimestamp']='2026-10-28T18:00:00Z';r['metadata']['generation']+=1
  emit(r);raise SystemExit(0)
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


def test_accepted_deletion_is_saved_before_an_interrupted_repair_wait(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "interrupt-after-delete"}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-finalizer-updates",
            str(tmp_path / "workload.log"),
            "--ready-timeout",
            "2",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == -9
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    mutation = evidence["deletions"][0]
    assert len(evidence["deletions"]) == 1
    assert (
        mutation["response"]["details"]["uid"]
        == mutation["resource"]["metadata"]["uid"]
    )


def test_workload_waits_for_the_initial_owned_service_after_deployment_readiness(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "delayed-service"}))
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
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert len(evidence["deletions"]) == 4
    updates = json.loads((tmp_path / "workload-updates.json").read_text())
    assert [
        u["response"]["metadata"]["annotations"]["sieve.testing/phase"]
        for u in updates["updates"]
    ] == list("ABCD")
    assert json.loads(state.read_text())["service_reads"] == 3


@pytest.mark.parametrize("scenario", ["missing-service", "foreign-service"])
def test_initial_service_wait_rejects_missing_or_foreign_children(
    tmp_path, kubernetes_boundary, scenario
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": scenario}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-finalizer-updates",
            str(tmp_path / "workload.log"),
            "--poll-interval",
            "0.01",
            "--ready-timeout",
            "2",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["status"] == "harness_or_setup_failure"
    assert evidence["error"] == (
        "Timed out: initial Pooler service"
        if scenario == "missing-service"
        else "Pooler child ownership is unproven"
    )
    assert not evidence["deletions"]
    assert not (tmp_path / "semantic-outcome.json").exists()


def test_initial_service_response_after_readiness_deadline_cannot_start_mutations(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "slow-service"}))
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
            "1",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["status"] == "harness_or_setup_failure"
    assert evidence["error"] == "Timed out: initial Pooler service"
    assert not evidence["deletions"]
    updates = json.loads((tmp_path / "workload-updates.json").read_text())
    assert not updates["updates"]
    assert not (tmp_path / "semantic-outcome.json").exists()


def test_initial_service_polling_interval_cannot_extend_readiness_deadline(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "missing-service"}))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "pooler-finalizer-updates",
            str(tmp_path / "workload.log"),
            "--poll-interval",
            "10",
            "--ready-timeout",
            "1",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["error"] == "Timed out: initial Pooler service"
    assert not evidence["deletions"]
    updates = json.loads((tmp_path / "workload-updates.json").read_text())
    assert not updates["updates"]
    assert not (tmp_path / "semantic-outcome.json").exists()


def test_workload_retains_rejected_admission_and_retries_after_controller_restart(
    tmp_path, kubernetes_boundary
):
    state, environment = kubernetes_boundary
    state.write_text(json.dumps({"scenario": "webhook-restarts"}))
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
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    updates = json.loads((tmp_path / "workload-updates.json").read_text())
    assert [
        u["state"]["metadata"]["annotations"]["sieve.testing/phase"]
        for u in updates["updates"]
    ] == list("ABCD")
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert len(evidence["api_retries"]) == 1
    rejected = tmp_path / evidence["api_retries"][0]["command_log"]
    assert (
        "connect: connection refused" in rejected.with_suffix(".stderr.log").read_text()
    )
    assert json.loads(state.read_text())["webhook_refusals"] == 1


@pytest.mark.parametrize("scenario", ["webhook-denied", "webhook-unavailable"])
def test_admission_retry_fails_closed_for_policy_rejection_or_expired_budget(
    tmp_path, kubernetes_boundary, scenario
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
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
    assert evidence["status"] == "harness_or_setup_failure"
    assert not (tmp_path / "semantic-outcome.json").exists()
    if scenario == "webhook-denied":
        assert json.loads(state.read_text())["webhook_refusals"] == 1
        assert not evidence.get("api_retries")
    else:
        assert len(evidence["api_retries"]) > 1
    updates = json.loads((tmp_path / "workload-updates.json").read_text())
    assert [
        u["state"]["metadata"]["annotations"]["sieve.testing/phase"]
        for u in updates["updates"]
    ] == ["A"]


@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("fixed", 0),
        ("buggy", 1),
        ("lost-data", 1),
        ("transient", 1),
        ("stalled", 1),
        ("unexpected-generation", 1),
    ],
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
    if scenario in ("stalled", "unexpected-generation"):
        evidence = json.loads((tmp_path / "benchmark-observations.json").read_text())
        assert evidence["status"] == "harness_or_setup_failure"
        assert (
            "watch did not acknowledge"
            if scenario == "stalled"
            else "update changed undeclared"
        ) in evidence["error"]
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
