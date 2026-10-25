"""Bounded subprocesses with explicit ownership, deadlines and durable logs."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import itertools
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

DEFAULT_TIMEOUTS = {
    "command": 60,
    "network": 600,
    "compile": 900,
    "build": 2700,
    "cluster": 600,
    "workload": 1800,
    "request": 30,
    "cleanup": 120,
    "terminate": 10,
}
_settings = ContextVar("sieve_process_settings", default=None)
_deadline = ContextVar("sieve_process_deadline", default=None)
_counter = itertools.count(1)


def validate_timeouts(overrides):
    if not isinstance(overrides, dict):
        raise ValueError("process_timeouts must be a dictionary")
    if unknown := set(overrides) - set(DEFAULT_TIMEOUTS):
        raise ValueError("Unknown process timeout: " + ", ".join(sorted(unknown)))
    for name, value in overrides.items():
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Process timeout {name} must be a finite positive number")
    return {**DEFAULT_TIMEOUTS, **overrides}


def settings():
    current = _settings.get()
    if current is None:
        from sieve_tool.common.config import get_common_config

        current = (
            validate_timeouts(get_common_config().process_timeouts),
            Path(tempfile.gettempdir()) / "sieve-commands" / uuid4().hex,
        )
        _settings.set(current)
    return current


def limit(category):
    return settings()[0][category]


def remaining(seconds):
    deadline = _deadline.get()
    return (
        min(seconds, max(0, deadline - time.monotonic()))
        if deadline is not None
        else seconds
    )


def request_timeout():
    seconds = remaining(limit("request"))
    if seconds <= 0:
        raise ProcessTimeout("Kubernetes request", seconds, None)
    return (min(5, seconds), seconds)


@contextmanager
def execution(timeouts=None, log_dir=None):
    inherited, inherited_dir = settings()
    policy = validate_timeouts({**inherited, **(timeouts or {})})
    token = _settings.set(
        (policy, Path(log_dir) if log_dir is not None else inherited_dir)
    )
    try:
        yield
    finally:
        _settings.reset(token)


@contextmanager
def deadline(seconds, *, fresh=False):
    end = time.monotonic() + seconds
    if not fresh and _deadline.get() is not None:
        end = min(end, _deadline.get())
    token = _deadline.set(end)
    try:
        yield
    finally:
        _deadline.reset(token)


def bounded(category):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with deadline(limit(category)):
                return function(*args, **kwargs)

        return call

    return decorate


class Cancelled(KeyboardInterrupt):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"Cancelled by signal {signum}")


@contextmanager
def cancellation_signals():
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(signum, frame):
        raise Cancelled(signum)

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class ProcessError(RuntimeError):
    def __init__(self, operation, returncode, log):
        self.operation, self.returncode, self.log = operation, returncode, log
        super().__init__(f"{operation} failed (exit {returncode}); logs: {log}")


class ProcessTimeout(ProcessError):
    def __init__(self, operation, seconds, log):
        super().__init__(operation, None, log)
        self.seconds = seconds
        self.args = (f"{operation} exceeded its {seconds:g}s deadline; logs: {log}",)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str
    duration: float
    log: Path


class ManagedProcess:
    def __init__(
        self,
        argv,
        *,
        operation,
        category="command",
        timeout=None,
        cwd=None,
        env=None,
        output=None,
    ):
        if isinstance(argv, (str, bytes)) or not argv:
            raise TypeError("Commands require a nonempty argument list")
        self.operation = operation
        self.timeout = remaining(limit(category) if timeout is None else timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ProcessTimeout(operation, self.timeout, None)
        self.started = time.monotonic()
        self.closed = False
        self.proc = None
        _, directory = settings()
        directory.mkdir(parents=True, exist_ok=True)
        self.log = directory / f"{next(_counter):04d}-{uuid4().hex[:8]}"
        self.events = directory / "commands.jsonl"
        self.out_path = (
            Path(output) if output is not None else self.log.with_suffix(".stdout.log")
        )
        self.err_path = self.log.with_suffix(".stderr.log")
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out = self.err = None
        try:
            self.out = self.out_path.open("wb")
            self.err = self.err_path.open("wb")
            self.proc = subprocess.Popen(
                [os.fspath(arg) for arg in argv],
                cwd=cwd,
                env={**os.environ, **env} if env is not None else None,
                stdout=self.out,
                stderr=self.err,
                start_new_session=True,
            )
            print(
                f"[sieve] {operation}: started (limit {self.timeout:g}s; logs {self.log})",
                flush=True,
            )
            self.event(
                "started",
                timeout_seconds=self.timeout,
                cwd=str(Path(cwd or Path.cwd()).resolve()),
            )
        except BaseException:
            self.close("start_failed")
            raise

    def event(self, status, **fields):
        # Labels, never environment values or full argv (which can carry secrets).
        data = {
            "operation": self.operation,
            "status": status,
            "pid": self.proc.pid if self.proc is not None else None,
            "elapsed_seconds": time.monotonic() - self.started,
            "stdout_log": str(self.out_path),
            "stderr_log": str(self.err_path),
            **fields,
        }
        with self.events.open("a") as stream:
            stream.write(json.dumps(data) + "\n")

    def group_alive(self):
        try:
            os.killpg(self.proc.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Darwin can report EPERM for a group whose leader just exited.
            # Live owned descendants still keep that group signalable.
            return self.proc.poll() is None

    def terminate(self):
        if self.proc is None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        end = time.monotonic() + remaining(limit("terminate"))
        self.proc.poll()
        while self.group_alive() and time.monotonic() < end:
            self.proc.poll()
            time.sleep(min(0.05, max(0, end - time.monotonic())))
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # A Darwin process can be exiting but not yet waitable.
            if self.proc.poll() is None:
                self.proc.wait(timeout=2)
        self.proc.wait(timeout=2)

    def close(self, status="stopped"):
        if self.closed:
            return
        self.closed = True
        primary = sys.exception()
        try:
            self.terminate()
        except Exception as error:
            if primary is None:
                raise
            primary.add_note(f"Process cleanup also failed: {error}")
        finally:
            for stream in (self.out, self.err):
                if stream is not None:
                    stream.close()
            try:
                self.event(
                    status,
                    returncode=self.proc.returncode if self.proc is not None else None,
                )
            except OSError:
                # A full/unavailable log volume must not hide the original failure.
                pass

    def wait(
        self, *, check=True, capture_output=False, max_capture_bytes=8 * 1024 * 1024
    ):
        status = "failed"
        try:
            try:
                seconds = max(0, self.timeout - (time.monotonic() - self.started))
                code = self.proc.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                status = "timed_out"
                raise ProcessTimeout(self.operation, self.timeout, self.log) from None
            status = "completed" if code == 0 else "failed"
            if check and code:
                raise ProcessError(self.operation, code, self.log)
        except KeyboardInterrupt, SystemExit:
            status = "cancelled"
            raise
        finally:
            self.close(status)
        out = err = ""
        if capture_output:
            if (
                max(self.out_path.stat().st_size, self.err_path.stat().st_size)
                > max_capture_bytes
            ):
                raise ProcessError(
                    self.operation + " output exceeds capture limit", code, self.log
                )
            out = self.out_path.read_text(errors="replace")
            err = self.err_path.read_text(errors="replace")
        return Result(code, out, err, time.monotonic() - self.started, self.log)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, tb):
        self.close("cancelled" if kind else "stopped")


def run(argv, *, check=True, capture_output=False, **kwargs):
    with ManagedProcess(argv, **kwargs) as child:
        return child.wait(check=check, capture_output=capture_output)
