"""Exercise actual process groups; mocks cannot establish cancellation safety."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from sieve_tool.process import (
    ProcessError,
    ProcessTimeout,
    deadline,
    execution,
    run,
    validate_timeouts,
)


def test_arguments_cwd_environment_and_large_file_output(tmp_path):
    caller = Path.cwd()
    literal = 'spaces; $(touch SHOULD_NOT_EXIST) "quoted"'
    with execution(log_dir=tmp_path / "logs"):
        result = run(
            [
                sys.executable,
                "-c",
                'import os,sys;print(sys.argv[1]);print(os.getcwd());print(os.getenv("SIEVE_TEST_VALUE"))',
                literal,
            ],
            operation="literal arguments",
            cwd=tmp_path,
            env={"SIEVE_TEST_VALUE": "value"},
            capture_output=True,
        )
        assert result.stdout.splitlines() == [literal, str(tmp_path), "value"]
        large = run(
            [sys.executable, "-c", 'import sys;sys.stdout.write("x"*10000000)'],
            operation="large output",
        )
        assert large.log.with_suffix(".stdout.log").stat().st_size == 10000000
    assert Path.cwd() == caller
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_nonzero_exit_preserves_logs_and_expectations(tmp_path):
    with execution(log_dir=tmp_path):
        with pytest.raises(ProcessError) as caught:
            run(
                [
                    sys.executable,
                    "-c",
                    'import sys;print("evidence",file=sys.stderr);sys.exit(7)',
                ],
                operation="failing command",
            )
        assert caught.value.returncode == 7
        assert (
            caught.value.log.with_suffix(".stderr.log").read_text().strip()
            == "evidence"
        )
        result = run(
            [sys.executable, "-c", "raise SystemExit(3)"],
            operation="expected probe failure",
            check=False,
        )
        assert result.returncode == 3
    records = [
        json.loads(line)
        for line in (tmp_path / "commands.jsonl").read_text().splitlines()
    ]
    assert [r["status"] for r in records] == ["started", "failed", "started", "failed"]
    assert all("env" not in r and "argv" not in r for r in records)


def running(pid):
    # PID 1 in containers can leave adopted zombies; they are not running work.
    status = Path(f"/proc/{pid}/stat")
    if status.exists() and status.read_text().split(") ", 1)[1].startswith("Z"):
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_timeout_kills_ignored_signal_in_grandchild(tmp_path):
    pid_file = tmp_path / "grandchild.pid"
    code = """import os,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])
open(sys.argv[1],'w').write(str(child.pid))
time.sleep(60)
"""
    started = time.monotonic()
    with execution({"terminate": 0.1}, tmp_path / "logs"):
        with pytest.raises(ProcessTimeout):
            run(
                [sys.executable, "-c", code, str(pid_file)],
                operation="hung tree",
                timeout=0.6,
            )
    assert time.monotonic() - started < 5
    pid = int(pid_file.read_text())
    end = time.monotonic() + 2
    while running(pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert not running(pid)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_cancellation_reaps_child_and_restores_signal_handler(tmp_path, signum):
    marker = tmp_path / "pid"
    parent = """import os,signal,sys
from sieve_tool.process import run,execution,cancellation_signals,Cancelled
try:
 with execution({'terminate':0.1},sys.argv[2]), cancellation_signals():
  run([sys.executable,'-c','import os,sys,time;open(sys.argv[1],"w").write(str(os.getpid()));time.sleep(60)',sys.argv[1]],operation='cancel me')
except Cancelled as error:
 assert signal.getsignal(signal.SIGTERM)==signal.SIG_DFL
 raise SystemExit(128+error.signum)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", parent, str(marker), str(tmp_path / "logs")],
        start_new_session=True,
    )
    try:
        end = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < end:
            time.sleep(0.02)
        assert marker.exists()
        child = int(marker.read_text())
        proc.send_signal(signum)
        assert proc.wait(timeout=5) == 128 + signum
        assert not running(child)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def test_deadline_is_shared_and_invalid_limits_fail_closed(tmp_path):
    for value in [0, -1, True, float("inf"), float("nan"), "10"]:
        with pytest.raises(ValueError):
            validate_timeouts({"build": value})
    with pytest.raises(ValueError):
        validate_timeouts({"typo": 10})
    with execution({"terminate": 0.05}, tmp_path), deadline(0.2):
        with pytest.raises(ProcessTimeout):
            run(
                [sys.executable, "-c", "import time;time.sleep(10)"],
                operation="parent budget",
                timeout=60,
            )


def test_outer_timeout_reaches_nested_managed_groups(tmp_path):
    marker = tmp_path / "nested.pid"
    nested = """import sys
from sieve_tool.process import run,execution
with execution({'terminate':0.1},sys.argv[2]):
 run([sys.executable,'-c','import os,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open(sys.argv[1],"w").write(str(os.getpid()));time.sleep(60)',sys.argv[1]],operation='nested group',timeout=60)
"""
    with execution({"terminate": 0.2}, tmp_path / "outer"):
        with pytest.raises(ProcessTimeout):
            run(
                [sys.executable, "-c", nested, str(marker), str(tmp_path / "inner")],
                operation="whole workload",
                timeout=1.5,
            )
    assert marker.exists()
    pid = int(marker.read_text())
    end = time.monotonic() + 2
    while running(pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert not running(pid)


def test_quiet_work_and_stdin_are_supported(tmp_path):
    with execution(log_dir=tmp_path):
        result = run(
            [
                sys.executable,
                "-c",
                "import sys,time;time.sleep(0.3);print(sys.stdin.read())",
            ],
            operation="quiet command",
            timeout=2,
            input="literal $() input",
            capture_output=True,
        )
    assert result.stdout.strip() == "literal $() input"


def test_successful_parent_cannot_leave_background_work(tmp_path):
    marker = tmp_path / "pid"
    code = """import subprocess,sys,time
p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
open(sys.argv[1],'w').write(str(p.pid))
"""
    with execution({"terminate": 0.1}, tmp_path / "logs"):
        run([sys.executable, "-c", code, str(marker)], operation="background leak")
    pid = int(marker.read_text())
    end = time.monotonic() + 2
    while running(pid) and time.monotonic() < end:
        time.sleep(0.02)
    assert not running(pid)


def test_expired_operation_still_allows_a_graceful_shutdown(tmp_path):
    marker = tmp_path / "graceful"
    code = """import signal,sys,time
from pathlib import Path
def stop(*args):
 time.sleep(0.05)
 Path(sys.argv[1]).write_text('flushed')
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
while True:time.sleep(1)
"""
    with execution({"terminate": 0.3}, tmp_path / "logs"), deadline(0.4):
        with pytest.raises(ProcessTimeout):
            run(
                [sys.executable, "-c", code, str(marker)],
                operation="grace after deadline",
                timeout=10,
            )
    assert marker.read_text() == "flushed"
