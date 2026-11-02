"""Exercise sequence workload commands at the external Kubernetes seam."""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sequence_cluster(tmp_path):
    state = tmp_path / "cluster.json"
    state.write_text(json.dumps({"resources": {}, "revision": 0}))
    executable = tmp_path / "kubectl"
    executable.write_text(
        f"#!{sys.executable}\n"
        + r"""
import base64,copy,json,os,sys
from pathlib import Path
path=Path(os.environ['SEQUENCE_TEST_CLUSTER']);cluster=json.loads(path.read_text());resources=cluster['resources']
args=sys.argv[1:]
if args[:2]==['--namespace','default']:args=args[2:]
def save(obj):
    kind=obj['kind'].lower();meta=obj.setdefault('metadata',{});meta.setdefault('namespace','default');meta.setdefault('uid',kind+'-uid');meta.setdefault('generation',1)
    cluster['revision']+=1;meta['resourceVersion']=str(cluster['revision']);resources[kind]=obj;return obj
def reconcile():
    issuer,certificate=resources.get('issuer'),resources.get('certificate')
    if issuer:issuer['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':1}]}
    if certificate:
        generation=certificate['metadata']['generation'];certificate['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':generation}]}
        if cluster.get('reuse_certificate') and generation>=2:return
        host=certificate['spec']['dnsNames'][0];material=json.loads(Path(os.environ['SEQUENCE_TEST_TLS']).read_text())[host]
        save({'apiVersion':'v1','kind':'Secret','metadata':{'name':certificate['metadata']['name'],'uid':'child-uid','ownerReferences':[{'uid':certificate['metadata']['uid'],'name':certificate['metadata']['name'],'kind':'Certificate','controller':True}]},'data':material})
    store,parent=resources.get('secretstore'),resources.get('externalsecret')
    if store:store['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':1}]}
    if not store or not parent:return
    generation=parent['metadata']['generation']
    parent['status']={'conditions':[{'type':'Ready','status':'True','observedGeneration':generation}],'syncedResourceVersion':str(generation)+'-fixture'}
    if cluster.get('stall_after_updates') and generation>=3:
        parent['status']['conditions'][0]['status']='False';return
    if cluster.get('lie_ready_once') and generation==2:
        cluster['heal_next_child_read']=True;return
    version=parent['spec']['data'][0]['remoteRef']['version']
    value=next(x['value'] for x in store['spec']['provider']['fake']['data'] if x['version']==version)
    save({'apiVersion':'v1','kind':'Secret','metadata':{'name':parent['metadata']['name'],'uid':'child-uid','ownerReferences':[{'uid':parent['metadata']['uid'],'name':parent['metadata']['name'],'kind':'ExternalSecret','controller':True}]},'data':{'marker':base64.b64encode(value.encode()).decode()}})
if args[0]=='apply':
    result=copy.deepcopy(save(json.load(sys.stdin)));reconcile()
elif args[0]=='get':
    if args[1]=='pods':
        operator=args[args.index('-l')+1].split('=',1)[1];workers=cluster.get('controller_workers',10)
        flag=('--concurrent-workers=' if operator=='cert-manager' else '--concurrent=')+str(workers)
        result={'items':[{'kind':'Pod','metadata':{'name':'controller','uid':'controller-uid'},'spec':{'containers':[{'name':'controller','args':[flag]}]},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'False' if cluster.get('controller_not_ready') else 'True'}]}}]}
    else:result=copy.deepcopy(resources.get(args[1].lower()))
    if args[1].lower()=='secret' and cluster.get('heal_next_child_read'):
        cluster['heal_next_child_read']=False;cluster['lie_ready_once']=False;reconcile()
elif args[0]=='patch':
    if cluster.get('reject_updates'):
        print('injected Kubernetes update rejection',file=sys.stderr);raise SystemExit(1)
    obj=resources[args[1].lower()];old=copy.deepcopy(obj)
    for change in json.loads(args[args.index('-p')+1]):
        parts=[s.replace('~1','/').replace('~0','~') for s in change['path'].split('/')[1:]];current=obj
        for part in parts[:-1]:current=current[int(part)] if isinstance(current,list) else current[part]
        key=int(parts[-1]) if isinstance(current,list) else parts[-1]
        if change['op']=='test':assert current[key]==change['value']
        elif not (cluster.get('ignore_updates') and change['path'].startswith('/spec/')):current[key]=change['value']
    if obj.get('spec')!=old.get('spec'):obj['metadata']['generation']+=1
    result=copy.deepcopy(save(obj));reconcile()
else:raise SystemExit('unsupported external command: '+repr(args))
path.write_text(json.dumps(cluster))
if result is not None:print(json.dumps(result))
"""
    )
    executable.chmod(0o755)
    openssl = shutil.which("openssl")
    assert openssl is not None
    return {
        "PATH": os.pathsep.join((str(tmp_path), str(Path(openssl).parent), os.defpath)),
        "PYTHONPATH": str(ROOT / "src"),
        "SEQUENCE_TEST_CLUSTER": str(state),
    }


@pytest.fixture
def sequence_tls(tmp_path):
    material = {}
    openssl = shutil.which("openssl")
    assert openssl is not None
    for index, host in enumerate(
        (
            "managed-child.example.test",
            "sequence-b.example.test",
            "sequence-c.example.test",
        )
    ):
        key, cert = tmp_path / f"key-{index}.pem", tmp_path / f"cert-{index}.pem"
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
                "subjectAltName=DNS:" + host,
                "-days",
                "1",
                "-out",
                str(cert),
            ],
            env={"PATH": os.defpath},
            capture_output=True,
            check=True,
            timeout=30,
        )
        material[host] = {
            "tls.crt": base64.b64encode(cert.read_bytes()).decode(),
            "tls.key": base64.b64encode(key.read_bytes()).decode(),
        }
    path = tmp_path / "tls.json"
    path.write_text(json.dumps(material))
    return str(path)


def invoke(operator, output, environment, *arguments):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "controllers" / operator / "workloads/workload.py"),
            "bounded-sequences",
            str(output),
            "--repair-timeout",
            "3",
            "--settle-seconds",
            "0",
            "--update-interval",
            "0.01",
            *arguments,
        ],
        env=environment,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_external_secrets_executes_a_generated_revert_and_checks_latest_state(
    tmp_path, sequence_cluster
):
    output = tmp_path / "run/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    plan = json.loads(output.with_name("sequence-plan.json").read_text())
    assert plan["selected"] == ["b", "c", "a"]
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "passed"
    assert [a["state"] for a in evidence["accepted"]] == ["a", "b", "c", "a"]
    assert evidence["accepted"][-1]["response"]["metadata"]["generation"] == 4
    assert evidence["samples"][-1]["effective_value"] == "expected-value"
    semantic = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert semantic["checks"] and all(c["passed"] for c in semantic["checks"])


def test_cert_manager_uses_the_same_generated_history_and_checks_actual_certificate_names(
    tmp_path, sequence_cluster, sequence_tls
):
    output = tmp_path / "certificate/workload.log"
    result = invoke(
        "cert-manager", output, {**sequence_cluster, "SEQUENCE_TEST_TLS": sequence_tls}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_text() == "FINISH-SIEVE-TEST\n"
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert [a["state"] for a in evidence["accepted"]] == ["a", "b", "c", "a"]
    assert evidence["accepted"][-1]["response"]["metadata"]["generation"] == 4
    assert evidence["samples"][-1]["effective_value"] == "managed-child.example.test"
    assert all(
        c["passed"]
        for c in json.loads(output.with_name("semantic-outcome.json").read_text())[
            "checks"
        ]
    )


def test_rejected_input_retains_progress_without_claiming_success(
    tmp_path, sequence_cluster
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["reject_updates"] = True
    state.write_text(json.dumps(cluster))
    output = tmp_path / "rejected/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 1
    assert not output.exists() or "FINISH-SIEVE-TEST" not in output.read_text()
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "input_failed"
    assert [a["state"] for a in evidence["accepted"]] == ["a"]
    assert evidence["error"]
    assert any(
        "rejection" in p.read_text()
        for p in output.parent.glob("sequence-commands/*.stderr.log")
    )
    semantic = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert not next(
        c["passed"]
        for c in semantic["checks"]
        if c["name"] == "declared_sequence_accepted"
    )


def test_transient_ready_with_old_data_is_retained_even_if_the_system_later_heals(
    tmp_path, sequence_cluster
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["lie_ready_once"] = True
    state.write_text(json.dumps(cluster))
    output = tmp_path / "lying/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "semantic_violation"
    assert [a["state"] for a in evidence["accepted"]] == ["a", "b"]
    assert evidence["samples"][-1]["effective_value"] == "expected-value"
    semantic = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert not next(
        c["passed"] for c in semantic["checks"] if c["name"] == "ready_means_latest"
    )


def test_successful_command_without_the_requested_input_is_an_input_failure(
    tmp_path, sequence_cluster
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["ignore_updates"] = True
    state.write_text(json.dumps(cluster))
    output = tmp_path / "ignored/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 1
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "input_failed"
    assert [a["state"] for a in evidence["accepted"]] == ["a"]
    assert evidence["samples"][-1]["verdict"]["status"] == "satisfied"


@pytest.mark.parametrize(
    "arguments",
    [
        ("--max-steps", "4"),
        ("--max-sequences", "17"),
        ("--sequence-index", "-1"),
        ("--sequence-index", "99"),
        ("--repair-timeout", "0"),
        ("--repair-timeout", "nan"),
        ("--update-interval", "-1"),
        ("--update-interval", "inf"),
        ("--settle-seconds", "-1"),
    ],
)
def test_invalid_limits_are_rejected_before_any_kubernetes_effect(
    tmp_path, sequence_cluster, arguments
):
    output = tmp_path / "invalid/workload.log"
    result = invoke("external-secrets", output, sequence_cluster, *arguments)
    assert result.returncode == 2
    assert not output.parent.exists()
    assert (
        json.loads(Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"]).read_text())[
            "resources"
        ]
        == {}
    )


def test_reusing_an_output_cannot_overwrite_any_previous_evidence(
    tmp_path, sequence_cluster
):
    output = tmp_path / "preserved/workload.log"
    first = invoke("external-secrets", output, sequence_cluster)
    assert first.returncode == 0, first.stdout + first.stderr
    before = {
        str(p.relative_to(output.parent)): p.read_bytes()
        for p in output.parent.rglob("*")
        if p.is_file()
    }
    cluster_before = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"]).read_bytes()
    again = invoke("external-secrets", output, sequence_cluster)
    assert again.returncode == 2
    assert before == {
        str(p.relative_to(output.parent)): p.read_bytes()
        for p in output.parent.rglob("*")
        if p.is_file()
    }
    assert (
        Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"]).read_bytes() == cluster_before
    )


@pytest.mark.parametrize("fault", ["old-certificate", "mismatched-key"])
def test_ready_certificate_requires_actual_latest_tls_material(
    tmp_path, sequence_cluster, sequence_tls, fault
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    if fault == "old-certificate":
        cluster = json.loads(state.read_text())
        cluster["reuse_certificate"] = True
        state.write_text(json.dumps(cluster))
    else:
        path = Path(sequence_tls)
        material = json.loads(path.read_text())
        material["sequence-b.example.test"]["tls.key"] = material[
            "managed-child.example.test"
        ]["tls.key"]
        path.write_text(json.dumps(material))
    output = tmp_path / "invalid-certificate/workload.log"
    result = invoke(
        "cert-manager", output, {**sequence_cluster, "SEQUENCE_TEST_TLS": sequence_tls}
    )
    assert result.returncode == 1
    assert "FINISH-SIEVE-TEST" not in output.read_text()
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "semantic_violation"
    assert evidence["samples"][-1]["ready_generation"] == 2
    assert evidence["samples"][-1]["verdict"]["reasons"] == ["ready_value"]


def test_nonconvergence_terminates_and_retains_the_complete_accepted_history(
    tmp_path, sequence_cluster
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["stall_after_updates"] = True
    state.write_text(json.dumps(cluster))
    output = tmp_path / "stalled/workload.log"
    result = invoke(
        "external-secrets", output, sequence_cluster, "--repair-timeout", "0.5"
    )
    assert result.returncode == 1
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "convergence_failed"
    assert [a["state"] for a in evidence["accepted"]] == ["a", "b", "c", "a"]
    assert evidence["samples"][-1]["verdict"]["status"] == "pending"
    semantic = json.loads(output.with_name("semantic-outcome.json").read_text())
    assert not next(
        c["passed"] for c in semantic["checks"] if c["name"] == "latest_state_stable"
    )


@pytest.mark.parametrize("workers", [1, 5])
def test_ten_reconciliation_workers_are_verified_before_sequence_updates(
    tmp_path, sequence_cluster, workers
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["controller_workers"] = workers
    state.write_text(json.dumps(cluster))
    output = tmp_path / "wrong-workers/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 1
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "configuration_failed"
    assert [a["state"] for a in evidence["accepted"]] == ["a"]
    assert evidence["samples"][-1]["controller_configured"] is False


def test_a_healthy_application_is_not_complete_while_the_controller_is_unready(
    tmp_path, sequence_cluster
):
    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    cluster = json.loads(state.read_text())
    cluster["controller_not_ready"] = True
    state.write_text(json.dumps(cluster))
    output = tmp_path / "unready-controller/workload.log"
    result = invoke(
        "external-secrets", output, sequence_cluster, "--repair-timeout", "0.5"
    )
    assert result.returncode == 1
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    assert evidence["status"] == "setup_failed"
    assert [a["state"] for a in evidence["accepted"]] == ["a"]
    assert evidence["samples"][-1]["effective_value"] == "expected-value"
    assert evidence["samples"][-1]["verdict"]["status"] == "pending"


@pytest.mark.parametrize("operator", ["external-secrets", "cert-manager"])
def test_plan_only_freezes_catalog_and_matched_plans_without_kubernetes_effects(
    tmp_path, sequence_cluster, operator
):
    from sieve_tool.plans import load_plan

    state = Path(sequence_cluster["SEQUENCE_TEST_CLUSTER"])
    before = state.read_bytes()
    output = tmp_path / operator / "workload.log"
    result = invoke(operator, output, sequence_cluster, "--plan-only")
    assert result.returncode == 0, result.stderr
    assert state.read_bytes() == before
    assert not output.exists()
    assert not output.with_name("semantic-outcome.json").exists()
    assert json.loads(output.with_name("sequence-plan.json").read_text())[
        "selected"
    ] == ["b", "c", "a"]
    assert load_plan(output.with_name("sequence-no-fault.yaml")).actions == []
    restart = load_plan(output.with_name("sequence-restart.yaml"))
    assert restart.actions[0].controller_label == operator
    assert (
        restart.actions[0]
        .trigger.definitions[0]
        .condition.resource_key.startswith(("externalsecret/", "certificate/"))
    )


def test_samples_bind_the_latest_accepted_input_and_its_order(
    tmp_path, sequence_cluster
):
    output = tmp_path / "ordered/workload.log"
    result = invoke("external-secrets", output, sequence_cluster)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(output.with_name("sequence-observations.json").read_text())
    for sample in evidence["samples"]:
        latest = [
            a
            for a in evidence["accepted"]
            if a["monotonic_ns"] <= sample["monotonic_ns"]
        ][-1]
        assert sample["expected_state"] == latest["state"]
        assert (
            sample["accepted_generation"]
            == latest["response"]["metadata"]["generation"]
        )
        assert sample["wall_ns"] >= latest["wall_ns"]
    assert [a["monotonic_ns"] for a in evidence["accepted"]] == sorted(
        a["monotonic_ns"] for a in evidence["accepted"]
    )
