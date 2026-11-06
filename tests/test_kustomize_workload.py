"""The public workload CLI rejects a controller outside the declared experiment."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_prune_workload_requires_ten_native_workers_before_mutating_cluster(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import json,sys\n"
        "assert sys.argv[1:] == ['--namespace','default','get','deployment',"
        "'kustomize-controller','--ignore-not-found','-o','json'], sys.argv\n"
        "print(json.dumps({'spec':{'template':{'spec':{'containers':["
        "{'name':'manager','args':['--concurrent=4']}]} }}}))\n"
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "controllers/kustomize-controller/workloads/workload.py"
            ),
            "prune-retry",
            str(output),
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(output.with_name("prune-observations.json").read_text())
    assert evidence["status"] == "configuration_failed"
    assert "--concurrent=10" in evidence["error"]
    assert output.read_text() == ""


def test_prune_workload_rejects_incomplete_native_source_api_installation(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        "a=sys.argv[3:]\n"
        "if a[:2]==['get','deployment']:\n"
        " print(json.dumps({'spec':{'template':{'spec':{'containers':["
        "{'name':'manager','args':['--concurrent=10']}]} }}}))\n"
        "else:\n"
        " assert a==['get','customresourcedefinitions','-o','json'],sys.argv\n"
        " print(json.dumps({'items':[{'metadata':{'name':n}} for n in "
        "['kustomizations.kustomize.toolkit.fluxcd.io',"
        "'gitrepositories.source.toolkit.fluxcd.io']]}))\n"
    )
    kubectl.chmod(0o700)
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "controllers/kustomize-controller/workloads/workload.py"
            ),
            "prune-retry",
            str(output),
        ],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(output.with_name("prune-observations.json").read_text())
    assert evidence["status"] == "configuration_failed"
    assert "ocirepositories.source.toolkit.fluxcd.io" in evidence["error"]
    assert "buckets.source.toolkit.fluxcd.io" in evidence["error"]
    assert output.read_text() == ""


def test_prune_workload_confirms_server_denial_then_observes_natural_recovery(tmp_path):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import copy,json,os,sys
from pathlib import Path
p=Path(os.environ['PRUNE_FIXTURE_STATE']);s=json.loads(p.read_text()) if p.exists() else {'resources':{},'calls':[]};r=s['resources'];a=sys.argv[3:];s['calls'].append(a)
P='sieve-prune';V='zz-sieve-prune-victim';A='sieve-prune-anchor';D='yy-sieve-prune-disabled';F='xx-sieve-prune-foreign';O='kustomize.toolkit.fluxcd.io/';rev='main@sha1:'+'2'*40
def save():p.write_text(json.dumps(s))
def out(x):save();print(json.dumps(x));sys.exit(0)
if a[:2]==['get','deployment'] and a[2]=='kustomize-controller':out({'spec':{'template':{'spec':{'containers':[{'name':'manager','args':['--concurrent=10']}]} }}})
if a[:2]==['get','customresourcedefinitions']:out({'items':[{'metadata':{'name':n}} for n in ['kustomizations.kustomize.toolkit.fluxcd.io']+[k+'.source.toolkit.fluxcd.io' for k in ['gitrepositories','buckets','ocirepositories','externalartifacts']]]})
if a[0]=='rollout':save();sys.exit(0)
if a[:2]==['apply','-f']:
 x=json.load(sys.stdin);kind=x['kind'].lower();name=x['metadata']['name'];x['metadata'].update(uid=kind+'-'+name,generation=1,namespace='default');r[kind+'/'+name]=x
 if kind=='kustomization':
  x['spec']['force']=False
  x['status']={'lastAppliedRevision':'main@sha1:'+'1'*40,'conditions':[{'type':'Ready','status':'True','observedGeneration':1}],'inventory':{'entries':[{'id':'default_'+n+'__ConfigMap','v':'v1'} for n in [V,A,D,F]]}}
  for n in [V,A,D,F]:
   r['configmap/'+n]={'kind':'ConfigMap','metadata':{'name':n,'uid':'uid-'+n,'labels':{O+'name':P,O+'namespace':'default'}},'data':{'value':'synthetic-'+n}}
  r['configmap/'+D]['metadata']['annotations']={O+'prune':'disabled'}
 out(x)
if a[0]=='patch':
 x=r[a[1]+'/'+a[2]];change=json.loads(a[a.index('-p')+1])
 if a[1]=='gitrepository':
  x.update(change)
  if x['status']['artifact']['revision']==rev:
   q=r['kustomization/'+P];q['status'].update(lastAttemptedRevision=rev,conditions=[{'type':'Ready','status':'False','reason':'PruneFailed','observedGeneration':1}]);q['status']['inventory']['entries']=[{'id':'default_'+n+'__ConfigMap','v':'v1'} for n in [A,V]]
 elif a[1]=='configmap':x['metadata']['labels'].update(change['metadata']['labels'])
 out(x)
if a[:2]==['delete','configmap']:
 save()
 if '-o' in a and a[a.index('-o')+1]!='name':print('error: unexpected -o output mode: json. We only support -o name',file=sys.stderr);sys.exit(1)
 assert '--dry-run=server' in a
 print('Forbidden: sieve synthetic prune deny',file=sys.stderr);sys.exit(1)
if a[0]=='delete':
 if a[1]=='validatingadmissionpolicybinding':
  del r['configmap/'+V];q=r['kustomization/'+P];q['status'].update(lastAppliedRevision=rev,conditions=[{'type':'Ready','status':'True','observedGeneration':1}]);q['status']['inventory']['entries']=[{'id':'default_'+A+'__ConfigMap','v':'v1'}]
 save();sys.exit(0)
if a[:2]==['get','pods']:out({'items':[{'metadata':{'uid':'controller-1'}}]})
if a[0]=='get':
 x=r.get(a[1]+'/'+a[2])
 if x is None:save();sys.exit(0)
 out(x)
raise SystemExit('Unexpected fixture command: '+repr(a))
"""
    )
    kubectl.chmod(0o700)
    state = tmp_path / "cluster.json"
    output = tmp_path / "results/workload.log"
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).resolve().parents[1]
                / "controllers/kustomize-controller/workloads/workload.py"
            ),
            "prune-retry",
            str(output),
            "--timeout",
            "4",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PRUNE_FIXTURE_STATE": str(state),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        result.stdout
        + result.stderr
        + output.with_name("prune-observations.json").read_text()
    )
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("prune-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert evidence["denial_probe"]["returncode"] == 1
    assert json.loads(output.with_name("semantic-outcome.json").read_text()) == {
        "schema_version": 1,
        "checks": [
            {"name": "rejected_prune_retains_inventory", "passed": True},
            {"name": "prune_recovers_after_denial_removed", "passed": True},
            {"name": "prune_policy_controls_preserved", "passed": True},
        ],
    }
    calls = json.loads(state.read_text())["calls"]
    assert not any(c[0] == "patch" and c[1] == "kustomization" for c in calls)
