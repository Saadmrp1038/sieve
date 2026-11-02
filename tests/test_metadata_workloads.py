"""Metadata workloads execute through their CLI and external Kubernetes seam."""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def invoke(
    operator, output, environment, *arguments, channel="labels", schedule="settled"
):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers" / operator / "workloads/workload.py"),
            f"metadata-{channel}-{schedule}",
            str(output),
            "--repair-timeout",
            "3",
            "--settle-seconds",
            "0",
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("channel", ["labels", "annotations"])
@pytest.mark.parametrize("schedule", ["settled", "overlap"])
def test_metadata_plan_freezes_operations_monitors_and_schedule_without_cluster_access(
    tmp_path, operator, channel, schedule
):
    from sieve_tool.plans import load_plan

    output = tmp_path / "plans/workload.log"
    result = invoke(
        operator,
        output,
        {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        "--plan-only",
        channel=channel,
        schedule=schedule,
    )
    assert result.returncode == 0, result.stderr
    plan = json.loads(output.with_name("metadata-plan.json").read_text())
    assert len(plan["generated"]) == 8
    assert plan["selected"]["channel"] == channel
    assert [s["operation"] for s in plan["selected"]["steps"]] == [
        "add",
        "change",
        "remove",
    ]
    assert plan["schedule"] == schedule
    assert plan["workers"] == 10
    assert plan["monitors"] == [
        "latest_state",
        "metadata_cleanup",
        "history_equivalence",
    ]
    assert load_plan(output.with_name("metadata-no-fault.yaml")).actions == []
    assert not output.exists()


@pytest.fixture
def metadata_cluster(tmp_path):
    state = tmp_path / "cluster.json"
    state.write_text(json.dumps({"resources": {}, "revision": 0}))
    executable = tmp_path / "kubectl"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import base64,copy,json,os,sys,time
from pathlib import Path
path=Path(os.environ['METADATA_TEST_CLUSTER']);cluster=json.loads(path.read_text());resources=cluster['resources'];args=sys.argv[1:]
if args[:2]==['--namespace','default']:args=args[2:]
def key(kind,name):return kind.lower()+'/'+name
def save(obj):
    meta=obj.setdefault('metadata',{});meta.setdefault('namespace','default');meta.setdefault('generation',1);meta.setdefault('uid',key(obj['kind'],meta['name'])+'-uid')
    cluster['revision']+=1;meta['resourceVersion']=str(cluster['revision']);resources[key(obj['kind'],meta['name'])]=obj;return obj
def reconcile(parent):
    kind=parent['kind'];meta=parent['metadata'];generation=meta['generation']
    parent['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':generation}],'syncedResourceVersion':str(generation)+'-fixture'}
    child=copy.deepcopy(resources.get(key('secret',meta['name']),{'apiVersion':'v1','kind':'Secret','metadata':{'name':meta['name'],'labels':{'unmanaged.example/keep':'external'},'annotations':{}},'data':{}}))
    child['metadata']['ownerReferences']=[{'uid':meta['uid'],'name':meta['name'],'kind':kind,'controller':True}]
    if kind=='Certificate':
        template=parent['spec']['secretTemplate'];child['data']=json.loads(Path(os.environ['METADATA_TEST_TLS']).read_text());child['type']='kubernetes.io/tls'
    else:
        template=parent['spec']['target']['template']['metadata'];child['data']={'marker':base64.b64encode(b'expected-value').decode()};child['type']='Opaque'
    if cluster.get('block_change_until_remove') and any(m.get('sieve.testing/history')=='two' for m in template.values()):return
    if cluster.get('wrong_owner'):child['metadata']['ownerReferences'][0]['uid']='wrong'
    oldmanaged=cluster.setdefault('managed',{}).get(meta['name'],{})
    for channel in ('labels','annotations'):
        values=child['metadata'].setdefault(channel,{})
        for name in oldmanaged.get(channel,[]):
            if not (cluster.get('sticky_cleanup') and name=='sieve.testing/history' and name not in template.get(channel,{})):values.pop(name,None)
        values.update(template.get(channel,{}))
        if cluster.get('drop_keep') and generation>=2:values.pop('sieve.testing/keep',None)
    cluster['managed'][meta['name']]={c:list(template.get(c,{})) for c in ('labels','annotations')}
    save(child)
if args[0] in ('apply','create'):
    obj=json.load(sys.stdin);result=copy.deepcopy(save(obj))
    if obj['kind'] in ('ExternalSecret','Certificate'):reconcile(obj)
    else:obj['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':1}]}
elif args[0]=='get':
    if args[1]=='pods':
        if cluster.get('delay_removal_pod_get') and any(o.get('metadata',{}).get('generation')==4 for o in resources.values()):time.sleep(1)
        operator=args[args.index('-l')+1].split('=',1)[1];workers=cluster.get('workers',10);flag='--concurrent-workers=' if operator=='cert-manager' else '--concurrent='
        result={'items':[{'metadata':{'name':'controller','uid':'controller-uid'},'spec':{'containers':[{'args':[flag+str(workers)]}]},'status':{'conditions':[{'type':'Ready','status':'True'}]}}]}
        if cluster.get('extra_unready'):
            other=copy.deepcopy(result['items'][0]);other['metadata']['uid']='other-controller';other['status']['conditions'][0]['status']='False';result['items'].append(other)
    else:
        result=copy.deepcopy(resources.get(key(args[1],args[2])))
        pending=cluster.get('pending',{}).get(args[2]) if args[1]=='secret' else None
        if pending:
            pending['reads']-=1
            if pending['reads']==0:resources[key('secret',args[2])]=pending['child'];del cluster['pending'][args[2]]
elif args[0]=='patch':
    obj=resources[key(args[1],args[2])];before=copy.deepcopy(obj);changes=json.loads(args[args.index('-p')+1]);cluster.setdefault('patches',[]).append(changes)
    for change in changes:
        parts=[v.replace('~1','/').replace('~0','~') for v in change['path'].split('/')[1:]];cursor=obj
        for part in parts[:-1]:cursor=cursor[part]
        name=parts[-1]
        if change['op']=='test':assert cursor[name]==change['value']
        elif cluster.get('ignore_updates') and change['path'].startswith('/spec/'):continue
        elif change['op']=='remove':del cursor[name]
        else:cursor[name]=change['value']
    if obj['spec']!=before['spec']:obj['metadata']['generation']+=1
    oldchild=copy.deepcopy(resources.get(key('secret',args[2])));result=copy.deepcopy(save(obj));reconcile(obj)
    if cluster.get('metadata_lag_reads') and oldchild:
        cluster.setdefault('pending',{})[args[2]]={'reads':cluster['metadata_lag_reads'],'child':copy.deepcopy(resources[key('secret',args[2])])};resources[key('secret',args[2])]=oldchild
    if cluster.get('emit_overlap_trace'):
        with Path(os.environ['METADATA_TEST_TRACE']).open('a') as trace:
            if obj['metadata']['generation']==2:
                trace.write('[SIEVE-BEFORE-RECONCILE]\tfixture.Reconcile\tcall-1\tSIEVE-V2:instance-1\n')
                trace.write('[SIEVE-AFTER-CACHE-READ]\tGet\t'+obj['kind'].lower()+'\tdefault\t'+args[2]+'\tfixture.Reconcile\tNoError\t'+json.dumps(result)+'\tSIEVE-V2:call-1\n')
            if obj['metadata']['generation']==3:
                trace.write('[SIEVE-BEFORE-HEAR]\t7\tUpdated\t'+obj['kind'].lower()+'\t'+json.dumps(result)+'\n[SIEVE-AFTER-HEAR]\t7\n')
            if obj['metadata']['generation']==4:trace.write('[SIEVE-AFTER-RECONCILE]\tfixture.Reconcile\tcall-1\tSIEVE-V2:instance-1\n')
    if cluster.get('bad_update_response'):
        field=cluster['bad_update_response'];result['metadata'][field]={'generation':2.5,'resourceVersion':before['metadata']['resourceVersion'],'name':'other','namespace':'other'}[field]
else:raise SystemExit('unsupported kubectl command '+repr(args))
path.write_text(json.dumps(cluster))
if result is not None:print(json.dumps(result))
"""
    )
    executable.chmod(0o755)
    openssl = shutil.which("openssl")
    assert openssl
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            str(key),
            "-subj",
            "/CN=managed-child.example.test",
            "-addext",
            "subjectAltName=DNS:managed-child.example.test",
            "-days",
            "1",
            "-out",
            str(cert),
        ],
        capture_output=True,
        check=True,
        timeout=30,
        env={"PATH": os.defpath},
    )
    tls = tmp_path / "tls.json"
    tls.write_text(
        json.dumps(
            {
                "tls.crt": base64.b64encode(cert.read_bytes()).decode(),
                "tls.key": base64.b64encode(key.read_bytes()).decode(),
            }
        )
    )
    return {
        "PATH": os.pathsep.join((str(tmp_path), str(Path(openssl).parent), os.defpath)),
        "PYTHONPATH": str(ROOT / "src"),
        "METADATA_TEST_CLUSTER": str(state),
        "METADATA_TEST_TLS": str(tls),
        "METADATA_TEST_TRACE": str(tmp_path / "sieve-server.log"),
    }


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("channel", ["labels", "annotations"])
def test_metadata_history_removes_only_managed_entry_and_matches_fresh_final_state(
    tmp_path, metadata_cluster, operator, channel
):
    output = tmp_path / "case/workload.log"
    result = invoke(operator, output, metadata_cluster, channel=channel)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert [a["operation"] for a in evidence["accepted"]] == [
        "initial",
        "add",
        "change",
        "remove",
        "fresh",
    ]
    assert (
        evidence["accepted"][0]["response"]["metadata"]["uid"]
        != evidence["accepted"][-1]["response"]["metadata"]["uid"]
    )
    final = evidence["samples"][-1]
    assert final["history_equivalence"]["status"] == "satisfied"
    assert (
        final["subject"]["child"]["metadata"][channel]["sieve.testing/keep"]
        == "retained"
    )
    assert "sieve.testing/history" not in final["subject"]["child"]["metadata"][channel]
    assert (
        final["subject"]["child"]["metadata"]["labels"]["unmanaged.example/keep"]
        == "external"
    )
    assert output.read_text() == "FINISH-SIEVE-TEST\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--repair-timeout", "nan"),
        ("--repair-timeout", "0"),
        ("--repair-timeout", "inf"),
        ("--settle-seconds", "-1"),
        ("--settle-seconds", "nan"),
    ],
)
def test_metadata_limits_are_validated_before_writing_plans_or_contacting_kubernetes(
    tmp_path, arguments
):
    output = tmp_path / "invalid/workload.log"
    result = invoke(
        "external-secrets",
        output,
        {"PATH": os.defpath, "PYTHONPATH": str(ROOT / "src")},
        "--plan-only",
        *arguments,
    )
    assert result.returncode == 2
    assert not output.parent.exists()


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize(
    "mode,status",
    [
        ("sticky_cleanup", "semantic_violation"),
        ("drop_keep", "semantic_violation"),
        ("ignore_updates", "input_failed"),
        ("workers", "configuration_failed"),
        ("wrong_owner", "semantic_violation"),
    ],
)
def test_failed_metadata_contract_retains_raw_progress_without_a_success_marker(
    tmp_path, metadata_cluster, operator, mode, status
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state[mode] = 5 if mode == "workers" else True
    path.write_text(json.dumps(state))
    output = tmp_path / "failed/workload.log"
    result = invoke(operator, output, metadata_cluster, "--repair-timeout", "1.5")
    assert result.returncode == 1, result.stdout + result.stderr
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == status, evidence.get("error")
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    assert not json.loads(output.with_name("semantic-outcome.json").read_text())[
        "checks"
    ][0]["passed"]
    if mode == "sticky_cleanup":
        assert [a["operation"] for a in evidence["accepted"]] == [
            "initial",
            "add",
            "change",
            "remove",
        ]
        assert evidence["samples"][-1]["subject"]["metadata_cleanup"]["reasons"] == [
            "metadata_cleanup"
        ]


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_current_ready_does_not_make_asynchronous_metadata_updates_an_immediate_violation(
    tmp_path, metadata_cluster, operator
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state["metadata_lag_reads"] = 1
    path.write_text(json.dumps(state))
    output = tmp_path / "lag/workload.log"
    result = invoke(operator, output, metadata_cluster)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert any(
        s["subject"]["latest_state"]["status"] == "satisfied"
        and s["subject"]["metadata_cleanup"]["status"] == "pending"
        for s in evidence["samples"]
    )


@pytest.mark.parametrize(
    "operator,kind,name",
    [
        ("external-secrets", "SecretStore", "sieve-metadata-store"),
        ("cert-manager", "Issuer", "sieve-metadata-issuer"),
    ],
)
def test_metadata_workload_refuses_a_preexisting_dependency_before_creating_parents(
    tmp_path, metadata_cluster, operator, kind, name
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state["resources"][kind.lower() + "/" + name] = {
        "kind": kind,
        "metadata": {"name": name, "uid": "old", "generation": 1},
        "status": {
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]
        },
    }
    path.write_text(json.dumps(state))
    output = tmp_path / "old-dependency/workload.log"
    result = invoke(operator, output, metadata_cluster)
    assert result.returncode == 1
    assert "dependency already exists" in output.read_text()
    assert set(json.loads(path.read_text())["resources"]) == {kind.lower() + "/" + name}


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
@pytest.mark.parametrize("channel", ["labels", "annotations"])
def test_overlap_schedule_can_submit_removal_before_change_converges_and_requires_separate_trace_proof(
    tmp_path, metadata_cluster, operator, channel
):
    from sieve_tool.testing.sequence_coverage import main as review_coverage

    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state.update(block_change_until_remove=True, emit_overlap_trace=True)
    path.write_text(json.dumps(state))
    output = tmp_path / "overlap/workload.log"
    result = invoke(
        operator, output, metadata_cluster, channel=channel, schedule="overlap"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observations = output.with_name("metadata-observations.json")
    evidence = json.loads(observations.read_text())
    assert evidence["overlap_status"] == "not_reviewed"
    coverage_path = output.with_name("metadata-coverage.json")
    assert (
        review_coverage(
            [
                metadata_cluster["METADATA_TEST_TRACE"],
                "--observations",
                str(observations),
                "--output",
                str(coverage_path),
            ]
        )
        == 0
    )
    coverage = json.loads(coverage_path.read_text())
    assert coverage["witnesses"][0]["older_input"] == 1
    assert coverage["witnesses"][0]["delivered_input"] == 2


def test_metadata_workload_preserves_copied_plan_and_refuses_to_reuse_result_artifacts(
    tmp_path, metadata_cluster
):
    output = tmp_path / "preserved/workload.log"
    output.parent.mkdir()
    plan = output.with_name("metadata-no-fault.yaml")
    plan.write_text(
        "workload: metadata-labels-settled\nactions: []\n# frozen execution input\n"
    )
    result = invoke("external-secrets", output, metadata_cluster)
    assert result.returncode == 0, result.stderr
    before = {
        str(p.relative_to(output.parent)): p.read_bytes()
        for p in output.parent.rglob("*")
        if p.is_file()
    }
    cluster_before = Path(metadata_cluster["METADATA_TEST_CLUSTER"]).read_bytes()
    again = invoke("external-secrets", output, metadata_cluster)
    assert again.returncode == 2
    assert before == {
        str(p.relative_to(output.parent)): p.read_bytes()
        for p in output.parent.rglob("*")
        if p.is_file()
    }
    assert (
        Path(metadata_cluster["METADATA_TEST_CLUSTER"]).read_bytes() == cluster_before
    )


@pytest.mark.parametrize(
    "field", ["generation", "resourceVersion", "name", "namespace"]
)
def test_malformed_update_acknowledgement_is_an_input_failure_with_raw_response_retained(
    tmp_path, metadata_cluster, field
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state["bad_update_response"] = field
    path.write_text(json.dumps(state))
    output = tmp_path / "bad-response/workload.log"
    result = invoke("external-secrets", output, metadata_cluster)
    assert result.returncode == 1
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == "input_failed"
    assert len(evidence["accepted"]) == 1
    assert any(
        '"sieve.testing/state": "metadata-1-add"' in p.read_text()
        for p in output.with_name("metadata-commands").glob("*.stdout.log")
    )


def test_one_ready_controller_cannot_hide_a_second_live_unready_controller(
    tmp_path, metadata_cluster
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state["extra_unready"] = True
    path.write_text(json.dumps(state))
    output = tmp_path / "ambiguous-controller/workload.log"
    result = invoke(
        "external-secrets", output, metadata_cluster, "--repair-timeout", "0.5"
    )
    assert result.returncode == 1
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == "setup_failed"
    assert evidence["samples"][-1]["verdict"]["reasons"] == ["controller_recovery"]


def test_cleanup_is_confirmed_after_a_deadline_expires_inside_an_external_request(
    tmp_path, metadata_cluster
):
    path = Path(metadata_cluster["METADATA_TEST_CLUSTER"])
    state = json.loads(path.read_text())
    state.update(sticky_cleanup=True, delay_removal_pod_get=True)
    path.write_text(json.dumps(state))
    output = tmp_path / "request-deadline/workload.log"
    result = invoke(
        "external-secrets", output, metadata_cluster, "--repair-timeout", "0.5"
    )
    assert result.returncode == 1
    evidence = json.loads(output.with_name("metadata-observations.json").read_text())
    assert evidence["status"] == "semantic_violation"
    assert evidence["samples"][-1]["subject"]["metadata_cleanup"]["reasons"] == [
        "metadata_cleanup"
    ]
