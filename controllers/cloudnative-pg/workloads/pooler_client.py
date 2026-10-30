"""Bounded, independent PostgreSQL client; every fresh connection is evidence."""

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

QUERY = "SELECT count(*),sum(id),count(*) FILTER (WHERE payload='ack-'||id::text) FROM sieve_benchmark;"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", choices=["pooler", "direct"], required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--duration", type=float, default=420)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--query-timeout", type=float, default=4)
    parser.add_argument(
        "--stop-file", type=Path, default=Path("/tmp/sieve-client-stop")
    )
    args = parser.parse_args()
    if any(
        not math.isfinite(v) or v <= 0
        for v in (args.duration, args.interval, args.query_timeout)
    ):
        parser.error("time limits must be finite and positive")
    end = time.monotonic() + args.duration
    count = 0
    while time.monotonic() < end and not args.stop_file.exists():
        started = time.time_ns()
        monotonic = time.monotonic_ns()
        timed_out = False
        try:
            result = subprocess.run(
                [
                    "psql",
                    "-X",
                    "-w",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-h",
                    args.host,
                    "-U",
                    "sieve_probe",
                    "-d",
                    "postgres",
                    "-Atc",
                    QUERY,
                ],
                capture_output=True,
                text=True,
                timeout=args.query_timeout,
                check=False,
            )
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            timed_out = True
            code = None
            stdout = (error.stdout or b"").decode(errors="replace")
            stderr = (error.stderr or b"").decode(errors="replace")
        count += 1
        print(
            json.dumps(
                {
                    "kind": "query",
                    "route": args.route,
                    "host": args.host,
                    "seq": count,
                    "started_ns": started,
                    "ended_ns": time.time_ns(),
                    "monotonic_started_ns": monotonic,
                    "monotonic_ended_ns": time.monotonic_ns(),
                    "returncode": code,
                    "timed_out": timed_out,
                    "stdout": stdout,
                    "stderr": stderr,
                    "ok": code == 0 and stdout.strip() == "32|528|32",
                }
            ),
            flush=True,
        )
        time.sleep(min(args.interval, max(0, end - time.monotonic())))
    print(
        json.dumps(
            {
                "kind": "terminal",
                "route": args.route,
                "attempts": count,
                "ended_ns": time.time_ns(),
                "reason": "stop" if args.stop_file.exists() else "duration",
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
