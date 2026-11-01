"""Continuous write receipts at the public client/psql command boundary."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "controllers/cloudnative-pg/workloads/rolling_client.py"


def test_writer_only_acknowledges_successful_commit_receipts(tmp_path):
    psql = tmp_path / "psql"
    psql.write_text(
        f"#!{sys.executable}\n"
        "import os,sys\nfrom pathlib import Path\n"
        "p=Path(os.environ['ATTEMPTS']);n=int(p.read_text())+1 if p.exists() else 1\n"
        "p.write_text(str(n))\n"
        "assert 'remote_apply' in sys.argv[-1] and 'COMMIT;' in sys.argv[-1]\n"
        "print('ACK:'+str(n))\n"
        "raise SystemExit(1 if n==2 else 0)\n"
    )
    psql.chmod(0o755)
    result = subprocess.run(
        [
            sys.executable,
            str(CLIENT),
            "--stream",
            "write",
            "--host",
            "db-rw",
            "--duration",
            "0.4",
            "--interval",
            "0.02",
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "ATTEMPTS": str(tmp_path / "attempts"),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) >= 4
    assert records[0]["acknowledged"] is True
    assert records[1]["acknowledged"] is False
    assert records[1]["outcome"] == "ambiguous"
    assert records[2]["seq"] == 3
    assert records[-1]["kind"] == "terminal"
    assert all(r["ended_ns"] >= r["started_ns"] for r in records[:-1])
