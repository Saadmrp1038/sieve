"""Partial application is adjudicated through the public workload CLI."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["lost", "retained", "control"])
def test_partial_application_collects_cleanup_evidence_after_inventory_loss(
    tmp_path, mode
):
    kubectl = tmp_path / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json,os,sys
from pathlib import Path
p=Path(os.environ['PARTIAL_FIXTURE_STATE']);s=json.loads(p.read_text()) if p.exists() else {'resources':{},'calls':[]};r=s['resources'];a=sys.argv[3:];s['calls'].append(a)
P='sieve-partial';N='sieve-partial-created';A='sieve-partial-anchor';O='kustomize.toolkit.fluxcd.io/'
mode=os.environ['PARTIAL_FIXTURE_MODE']
def out(x=None):
 p.write_text(json.dumps(s))
 if x is not None:print(json.dumps(x))
 sys.exit(0)
def obj(kind,name):return {'apiVersion':'v1','kind':kind,'metadata':{'name':name,'namespace':'default','uid':kind+'-'+name,'generation':1,'labels':{O+'name':P,O+'namespace':'default'}}}
if a[:2]==['get','deployment'] and a[2]=='kustomize-controller':out({'spec':{'template':{'spec':{'containers':[{'name':'manager','args':['--concurrent=10']}]} }}})
if a[:2]==['get','customresourcedefinitions']:out({'items':[{'metadata':{'name':n}} for n in ['kustomizations.kustomize.toolkit.fluxcd.io']+[k+'.source.toolkit.fluxcd.io' for k in ['gitrepositories','buckets','ocirepositories','externalartifacts']]]})
if a[0]=='rollout':out()
if a[:2]==['create','--dry-run=server']:
 if mode=='control':out({'kind':'ConfigMap'})
 p.write_text(json.dumps(s));print('Forbidden: sieve synthetic apply deny',file=sys.stderr);sys.exit(1)
if a[:2]==['apply','-f']:
 x=json.load(sys.stdin);kind=x['kind'].lower();name=x['metadata']['name'];x['metadata'].update(uid=kind+'-'+name,generation=1,namespace='default');r[kind+'/'+name]=x
 if kind=='kustomization':
  x['status']={'lastAppliedRevision':'main@sha1:'+'1'*40,'lastAttemptedRevision':'main@sha1:'+'1'*40,'conditions':[{'type':'Ready','status':'True','observedGeneration':1}],'inventory':{'entries':[{'id':'default_'+A+'__ConfigMap','v':'v1'}]}}
  r['configmap/'+A]=obj('ConfigMap',A)
 out(x)
if a[0]=='patch':
 x=r[a[1]+'/'+a[2]];change=json.loads(a[a.index('-p')+1])
 if a[1]=='gitrepository':
  x.update(change);rev=x['status']['artifact']['revision'];q=r.get('kustomization/'+P)
  if q and rev.endswith('2'*40):
   r['namespace/'+N]=obj('Namespace',N);q['status'].update(lastAttemptedRevision=rev,conditions=[{'type':'Ready','status':'False','reason':'ReconciliationFailed','message':'sieve synthetic apply deny','observedGeneration':1}])
   if mode!='lost':q['status']['inventory']['entries'].append({'id':'_'+N+'__Namespace','v':'v1'})
   if mode=='control':
    r['configmap/sieve-partial-blocked']=obj('ConfigMap','sieve-partial-blocked');q['status'].update(lastAppliedRevision=rev,conditions=[{'type':'Ready','status':'True','observedGeneration':1}])
  if q and rev.endswith('3'*40):
   q['status'].update(lastAppliedRevision=rev,lastAttemptedRevision=rev,conditions=[{'type':'Ready','status':'True','observedGeneration':1}])
   if mode!='lost':r.pop('namespace/'+N);r.pop('configmap/sieve-partial-blocked',None)
   q['status']['inventory']['entries']=[{'id':'default_'+A+'__ConfigMap','v':'v1'}]
 elif a[1]=='kustomization':
  x['metadata'].setdefault('annotations',{}).update(change['metadata']['annotations']);x['status']['lastHandledReconcileAt']=change['metadata']['annotations']['reconcile.fluxcd.io/requestedAt']
 out(x)
if a[0]=='delete':
 if a[1]=='kustomization':r.pop('kustomization/'+P);r.pop('configmap/'+A)
 else:r.pop(a[1]+'/'+a[2],None)
 out()
if a[:2]==['get','pods']:out({'items':[{'metadata':{'uid':'controller-1'},'status':{'containerStatuses':[{'name':'manager','restartCount':0}]}}]})
if a[0]=='get':out(r.get(a[1]+'/'+a[2]))
raise SystemExit('Unexpected fixture command: '+repr(a))
"""
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
            "partial-apply-control" if mode == "control" else "partial-apply-rejected",
            str(output),
            "--timeout",
            "2",
            "--settle-seconds",
            "0.02",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PARTIAL_FIXTURE_STATE": str(tmp_path / "cluster.json"),
            "PARTIAL_FIXTURE_MODE": mode,
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("partial-application.json").read_text())
    assert evidence["status"] == "completed"
    assert evidence["semantic_status"] == ("failed" if mode == "lost" else "passed")
    assert evidence["checks"] == {
        "created_namespace_tracked_after_apply": mode != "lost",
        "namespace_pruned_after_source_removal": mode != "lost",
        "namespace_absent_after_parent_finalization": mode != "lost",
    }
    assert evidence["final"]["parent"] is None
    if mode == "lost":
        assert (
            evidence["final"]["namespace"]["metadata"]["uid"]
            == "Namespace-sieve-partial-created"
        )
    else:
        assert evidence["final"]["namespace"] is None
    assert len(evidence["cleanup_reconciliations"]) == 3
    calls = json.loads((tmp_path / "cluster.json").read_text())["calls"]
    assert not any(c[:2] == ["delete", "namespace"] for c in calls)
