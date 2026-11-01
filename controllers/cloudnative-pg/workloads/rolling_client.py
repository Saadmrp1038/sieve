"""Numbered transactions and reads over fresh service connections."""

import argparse
import json
import math
import subprocess
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stream", choices=["read", "write"], required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--query-timeout", type=float, default=5)
    parser.add_argument("--stop-file", type=Path, default=Path("/tmp/sieve-stop"))
    args = parser.parse_args()
    if any(
        not math.isfinite(x) or x <= 0
        for x in (args.duration, args.interval, args.query_timeout)
    ):
        parser.error("time limits must be finite and positive")
    end = time.monotonic() + args.duration
    seq = 0
    while time.monotonic() < end and not args.stop_file.exists():
        seq += 1
        query = (
            "BEGIN; SET LOCAL synchronous_commit='remote_apply'; "
            f"INSERT INTO sieve_rolling VALUES ({seq},'ack-{seq}'); "
            f"COMMIT; SELECT 'ACK:{seq}';"
            if args.stream == "write"
            else "SELECT json_build_object('count',count(*),'max',max(id),"
            "'bad',count(*) FILTER (WHERE payload<>'ack-'||id::text)) "
            "FROM sieve_rolling;"
        )
        record = {
            "kind": "attempt",
            "stream": args.stream,
            "host": args.host,
            "seq": seq,
            "started_ns": time.time_ns(),
            "monotonic_started_ns": time.monotonic_ns(),
            "timeout_seconds": args.query_timeout,
            "timed_out": False,
        }
        try:
            result = subprocess.run(
                [
                    "psql",
                    "-X",
                    "-q",
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
                    query,
                ],
                capture_output=True,
                text=True,
                timeout=args.query_timeout,
                check=False,
            )
            record.update(
                returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
            )
        except subprocess.TimeoutExpired as error:
            record.update(
                returncode=None,
                timed_out=True,
                stdout=(error.stdout or b"").decode(errors="replace"),
                stderr=(error.stderr or b"").decode(errors="replace"),
            )
        ok = record["returncode"] == 0
        if args.stream == "write":
            ok = ok and str(record["stdout"]).strip() == f"ACK:{seq}"
        else:
            try:
                row = json.loads(str(record["stdout"]))
                ok = ok and row["bad"] == 0 and isinstance(row["count"], int)
            except (ValueError, KeyError, TypeError):
                ok = False
        record.update(
            ended_ns=time.time_ns(),
            monotonic_ended_ns=time.monotonic_ns(),
            ok=ok,
            acknowledged=ok and args.stream == "write",
            outcome="acknowledged"
            if ok and args.stream == "write"
            else "success"
            if ok
            else "ambiguous"
            if args.stream == "write"
            else "failed",
        )
        print(json.dumps(record), flush=True)
        time.sleep(min(args.interval, max(0, end - time.monotonic())))
    print(
        json.dumps(
            {
                "kind": "terminal",
                "stream": args.stream,
                "attempts": seq,
                "ended_ns": time.time_ns(),
                "reason": "stop" if args.stop_file.exists() else "duration",
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
