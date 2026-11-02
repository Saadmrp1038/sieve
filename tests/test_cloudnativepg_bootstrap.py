"""Certificate bootstrap through the workload CLI and external Kubernetes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bootstrap_boundary(tmp_path):
    state = tmp_path / "api.json"
    command = tmp_path / "kubectl"
    command.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, os, sys
from pathlib import Path
if sys.argv[1:]==['version','--client']:
 print('{}');raise SystemExit(0)
p=Path(os.environ['BOOTSTRAP_API']);s=json.loads(p.read_text());a=sys.argv[1:]
if a[:2]==['--namespace','default']:a=a[2:]
def emit(x):
 p.write_text(json.dumps(s));print(json.dumps(x))
def meta(name,uid):return {'name':name,'namespace':'default','uid':uid,'generation':1,'resourceVersion':'12'}
def owner():return [{'kind':'Cluster','name':'sieve-ca-bootstrap','uid':'cluster-uid','controller':True}]
if a[0]=='apply':
 r=json.load(sys.stdin);r['metadata'].update(meta(r['metadata']['name'],'cluster-uid'));s['cluster']=r;emit(r)
elif a[0]=='get':
 if a[1].startswith('clusters'):
  s['reads']=s.get('reads',0)+1
  r=s['cluster'];r['status']={'readyInstances':3,'instances':3,'currentPrimary':'db-1','targetPrimary':'db-1','phase':'Cluster in healthy state'}
  if s.get('scenario')=='unready-after-bootstrap' and s['reads']>1:r['status']['readyInstances']=2
  if s.get('scenario')=='bootstrap-error' and s['reads']==1:r['status'].update(readyInstances=0,phase='Unable to create required cluster objects',phaseReason='generating client CA certificate: secrets "sieve-ca-bootstrap-ca" already exists')
  emit(r)
 elif a[1]=='pods':
  emit({'items':[{'kind':'Pod','metadata':dict(meta('db-'+str(i),'pod-'+str(i)),ownerReferences=owner()),'status':{'conditions':[{'type':'Ready','status':'True'}]}} for i in range(1,4)]})
 elif a[1]=='secret':
  emit({'kind':'Secret','metadata':dict(meta(a[2],'secret-'+a[2]),ownerReferences=owner()),'data':{'ca.crt':'Y2E=','tls.crt':'bGVhZg=='}})
 else:raise SystemExit('unexpected get '+repr(a))
elif a[0]=='exec':
 q=a[-1]
 if 'pg_is_in_recovery' in q:print('f' if a[1]=='db-1' else 't')
 elif 'json_agg' in q:print(json.dumps([[i,'ack-'+str(i)] for i in range(1,33)]))
 else:print('')
else:raise SystemExit('unexpected command '+repr(a))
"""
    )
    command.chmod(0o755)
    subprocess.run([str(command), "version", "--client"], check=True, timeout=10)
    state.write_text("{}")
    return state, {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "BOOTSTRAP_API": str(state),
    }


def invoke(tmp_path, boundary):
    _, environment = boundary
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers/cloudnative-pg/workloads/workload.py"),
            "ca-secret-bootstrap",
            str(tmp_path / "workload.log"),
            "--ready-timeout",
            "5",
            "--observe-seconds",
            "0.02",
            "--poll-interval",
            "0.01",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
    )


def test_bootstrap_proves_three_owned_instances_and_acknowledged_data(
    tmp_path, bootstrap_boundary
):
    result = invoke(tmp_path, bootstrap_boundary)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads((tmp_path / "bootstrap-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert evidence["accepted_cluster"]["metadata"]["uid"] == "cluster-uid"
    assert evidence["database_rows"] == {
        name: [[i, "ack-" + str(i)] for i in range(1, 33)]
        for name in ("db-1", "db-2", "db-3")
    }
    assert evidence["database_recovery"] == {"db-1": "f", "db-2": "t", "db-3": "t"}
    assert "FINISH-SIEVE-TEST" in (tmp_path / "workload.log").read_text()


def test_bootstrap_rejects_readiness_lost_during_observation(
    tmp_path, bootstrap_boundary
):
    state, _ = bootstrap_boundary
    state.write_text(json.dumps({"scenario": "unready-after-bootstrap"}))
    result = invoke(tmp_path, bootstrap_boundary)
    assert result.returncode == 1
    outcome = json.loads((tmp_path / "semantic-outcome.json").read_text())
    assert {c["name"]: c["passed"] for c in outcome["checks"]}[
        "bootstrap_stays_ready"
    ] is False


def test_bootstrap_retains_transient_operator_error_after_recovery(
    tmp_path, bootstrap_boundary
):
    state, _ = bootstrap_boundary
    state.write_text(json.dumps({"scenario": "bootstrap-error"}))
    result = invoke(tmp_path, bootstrap_boundary)
    assert result.returncode == 1
    evidence = json.loads((tmp_path / "bootstrap-observations.json").read_text())
    assert evidence["status"] == "semantic_failure"
    assert evidence["ready_cluster"]["status"]["readyInstances"] == 3
    assert (
        "already exists"
        in evidence["auxiliary_object_errors"][0]["status"]["phaseReason"]
    )
