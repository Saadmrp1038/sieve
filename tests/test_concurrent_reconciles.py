"""Interleaved workers keep invocation identities and read dependencies."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from sieve_tool.analysis.analyze import generate_test_plans_from_learn_run
from sieve_tool.analysis.trace import assemble_trace
from sieve_tool.common.config import get_common_config, load_controller_config
from sieve_tool.events import ControllerWrite
from sieve_tool.trace import TraceError, read_trace


def test_overlapping_same_frame_writes_keep_their_own_cache_reads(tmp_path):
    path = tmp_path / "workers.log"
    path.write_text(
        "[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\t1\tSIEVE-V2:instance-a\n"
        "[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\t2\tSIEVE-V2:instance-b\n"
        '[SIEVE-AFTER-CACHE-READ]\tGet\tsecret\tdefault\ta\toperator.Reconcile\tNoError\t{"metadata":{"name":"a","namespace":"default"}}\tSIEVE-V2:1\n'
        '[SIEVE-AFTER-CACHE-READ]\tGet\tsecret\tdefault\tb\toperator.Reconcile\tNoError\t{"metadata":{"name":"b","namespace":"default"}}\tSIEVE-V2:2\n'
        "[SIEVE-BEFORE-REST-WRITE]\t11\n"
        "[SIEVE-AFTER-REST-WRITE]\t11\tUpdate\toperator.Reconcile\tNoError\tsecret\tdefault\tb\t{}\tSIEVE-V2:2\n"
        "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\t2\tSIEVE-V2:instance-b\n"
        "[SIEVE-BEFORE-REST-WRITE]\t12\n"
        "[SIEVE-AFTER-REST-WRITE]\t12\tUpdate\toperator.Reconcile\tNoError\tsecret\tdefault\ta\t{}\tSIEVE-V2:1\n"
        "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\t1\tSIEVE-V2:instance-a\n"
    )
    analyzed = assemble_trace(
        read_trace(path), compress_trivial=True, loosen_boundary=False
    )
    writes = [e for e in analyzed.reconciles if isinstance(e, ControllerWrite)]
    assert [(w.name, w.reconcile_id, w.read_keys) for w in writes] == [
        ("b", "2", {"secret/default/b"}),
        ("a", "1", {"secret/default/a"}),
    ]


def test_an_explicit_read_cannot_silently_lose_its_invocation(tmp_path):
    path = tmp_path / "orphan.log"
    path.write_text(
        "[SIEVE-AFTER-CACHE-READ]\tGet\tsecret\tdefault\ta\toperator.Reconcile\tNoError\t{}\tSIEVE-V2:missing\n"
    )
    with pytest.raises(TraceError, match="active invocation"):
        assemble_trace(read_trace(path), compress_trivial=True, loosen_boundary=False)


@pytest.mark.parametrize(
    "filename,diagnostic",
    [
        (
            "streamed-controller.log",
            "[SIEVE-TRACE-ERROR] unsupported runtime stack header",
        ),
        ("streamed-controller.log", "Sieve client RPC error: unmatched invocation"),
        ("sieve-server.log", "[SIEVE-TRACE-ERROR] unmatched invocation"),
    ],
)
def test_learning_rejects_an_observer_identity_failure(tmp_path, filename, diagnostic):
    (tmp_path / "mask.json").write_text("{}")
    (tmp_path / "sieve-server.log").write_text("")
    (tmp_path / filename).write_text(diagnostic + "\n")
    context = SimpleNamespace(
        result_dir=str(tmp_path),
        oracle_dir=str(tmp_path),
        result_root_dir=str(tmp_path),
        controller="kubernetes-secret-generator",
        test_workload="fixture",
        common_config=get_common_config(),
        controller_config=load_controller_config(
            Path(__file__).resolve().parents[1]
            / "controllers/kubernetes-secret-generator"
        ),
    )
    with pytest.raises(ValueError, match="observer"):
        generate_test_plans_from_learn_run(context)


def test_sequential_invocations_retain_the_previous_instance_boundary(tmp_path):
    path = tmp_path / "sequential.log"
    path.write_text(
        '[SIEVE-BEFORE-HEAR]\t1\tAdded\tsecret\t{"metadata":{"name":"a","namespace":"default"}}\n'
        "[SIEVE-AFTER-HEAR]\t1\n"
        "[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\t1\tSIEVE-V2:instance-a\n"
        "[SIEVE-BEFORE-REST-WRITE]\t1\n"
        "[SIEVE-AFTER-REST-WRITE]\t1\tUpdate\toperator.Reconcile\tNoError\tsecret\tdefault\ta\t{}\tSIEVE-V2:1\n"
        "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\t1\tSIEVE-V2:instance-a\n"
        "[SIEVE-BEFORE-RECONCILE]\toperator.Reconcile\t2\tSIEVE-V2:instance-a\n"
        "[SIEVE-BEFORE-REST-WRITE]\t2\n"
        "[SIEVE-AFTER-REST-WRITE]\t2\tUpdate\toperator.Reconcile\tNoError\tsecret\tdefault\ta\t{}\tSIEVE-V2:2\n"
        "[SIEVE-AFTER-RECONCILE]\toperator.Reconcile\t2\tSIEVE-V2:instance-a\n"
    )
    analyzed = assemble_trace(
        read_trace(path), compress_trivial=True, loosen_boundary=False
    )
    writes = [e for e in analyzed.reconciles if isinstance(e, ControllerWrite)]
    assert [w.range_start_timestamp for w in writes] == [-1, 2]
