"""Continuous client evidence through the executable/psql boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "controllers/cloudnative-pg/workloads/pooler_client.py"


def test_client_records_successful_queries_and_a_bounded_terminal_record(tmp_path):
    psql = tmp_path / "psql"
    psql.write_text("#!/bin/sh\nprintf '32|528|32\\n'\n")
    psql.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            str(CLIENT),
            "--route",
            "pooler",
            "--host",
            "pooler.test",
            "--duration",
            "1",
            "--interval",
            "0.01",
            "--query-timeout",
            "2",
        ],
        env={"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    attempts = records[:-1]
    assert attempts and all(r["ok"] and r["stdout"] == "32|528|32\n" for r in attempts)
    assert [r["seq"] for r in attempts] == list(range(1, len(attempts) + 1))
    assert all(r["ended_ns"] >= r["started_ns"] for r in attempts)
    assert records[-1]["kind"] == "terminal"
    assert records[-1]["attempts"] == len(attempts)
    assert records[-1]["reason"] == "duration"


@pytest.mark.parametrize(
    "script,code,timed_out,output",
    [
        ("echo 'connection refused' >&2; exit 2", 2, False, ""),
        ("printf '31|496|31\\n'", 0, False, "31|496|31\n"),
        ("sleep 0.3; printf '31|496|31\\n'", 0, False, "31|496|31\n"),
        ("exec sleep 3", None, True, ""),
    ],
)
def test_client_preserves_failures_and_finishes_within_its_budget(
    tmp_path, script, code, timed_out, output
):
    psql = tmp_path / "psql"
    psql.write_text("#!/bin/sh\n" + script + "\n")
    psql.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            str(CLIENT),
            "--route",
            "direct",
            "--host",
            "database.test",
            "--duration",
            "0.4",
            "--interval",
            "0.02",
            "--query-timeout",
            "0.2" if timed_out else "2",
        ],
        env={"PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[-1]["kind"] == "terminal"
    assert records[:-1] and all(not r["ok"] for r in records[:-1])
    assert all(
        r["returncode"] == code
        and r["timed_out"] == timed_out
        and r["stdout"] == output
        for r in records[:-1]
    )
    if code == 2:
        assert all("connection refused" in r["stderr"] for r in records[:-1])
