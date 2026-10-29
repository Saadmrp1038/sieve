# Process execution and timeouts

External commands use argument lists and explicit working directories. They write
stdout/stderr to files and start/end records to `commands.jsonl`. Records contain
operation labels, PIDs, durations, exit status and log locations; they do not dump
environments or full command arguments. Log paths in records are relative to the
commands directory, so they survive moving the first learning pass or archiving
results.

Build logs live under `.cache/commands/<run>/`. Workload and setup logs live in the
per-plan `commands/` directory. `workload-process.log` is the workload's stdout;
`workload.log` is its completion/error protocol. `process-outcome.json` records
whether the child completed, failed, timed out or was cancelled. Oracle alarms
and the actual injection/workload completion flags remain in the raw test JSON.
A nonzero child exit cannot be turned into success by a completion marker.

After a successful test workload, the harness waits for the fault schedule to
finish before collecting final evidence or stopping the coordinator. This wait
uses the finite `cluster` budget and ends early if the coordinator exits. Its
status is recorded separately in `injection-process-outcome.json`; the oracle
still requires the actual coordinator completion and read-pause evidence.
Failed workloads proceed directly to diagnostics. Learning and vanilla runs do
not perform this wait.
Before diagnostics, a failed test workload takes an immediate completion sample
so an already completed injection still has its own process outcome. An
unfinished schedule is marked `incomplete` without consuming the cluster wait
budget; this does not change the failed workload's exit code or completion flag.

## Default limits

| Policy | Default | Applies to |
|---|---:|---|
| `command` | 60 seconds | Short local commands and CLI queries |
| `network` | 10 minutes | Clones, dependency downloads, pulls and pushes |
| `compile` | 15 minutes | Host Go compilation and instrumentation |
| `build` | 45 minutes | One node/controller build, including nested operations |
| `cluster` | 10 minutes | Cluster setup, controller readiness or fault completion |
| `workload` | 30 minutes | One whole workload child process |
| `request` | 30 seconds | SDK calls and diagnostic commands |
| `cleanup` | 2 minutes | Shared final diagnostic collection and teardown budget |
| `terminate` | 10 seconds | Graceful shutdown before forced process termination |

These are wall-clock limits, not output-idle limits. Quiet builds keep running
within their deadline. Nested operations share the earlier enclosing deadline;
cluster creation retries cannot each restart the full setup budget. Diagnostics
reserve part of the cleanup budget for teardown. Process enumeration and final
reaping have small additional bounded OS waits.

The prior Secret Generator validation's slowest node build took about nine
minutes; its fault workloads took about two minutes including cluster startup.
The defaults provide room above that measured workload. Larger experiments can
choose explicit limits rather than silently running forever.

Use `sieve --config /path/to/settings.json ...` for global overrides, for example:

```json
{
  "process_timeouts": {
    "build": 3600,
    "workload": 2700
  },
  "workload_command_wait_timeout": 120
}
```

`workload_command_wait_timeout` remains the per-command budget for the built-in
workload driver; `workload_conditional_wait_timeout` controls its condition waits.
They are independent of the whole-workload deadline. A particular step can use
`.cmd(["kubectl", "rollout", "status", "statefulset/example", "--timeout=180s"],
timeout=190)` so its supervising deadline leaves time for the tool’s own error
message. This does not raise the limits on unrelated commands. Limits must be finite and
positive. Unknown process policies fail before execution.

A controller can override execution policies for one workload in its profile:

```json
{
  "test_setting": {
    "long-rebalance": {
      "process_timeouts": {"workload": 3600}
    }
  }
}
```

The selected settings are passed to the workload child. Controller
`test_command` entries and built-in `.cmd(...)` steps are argument arrays, for
example `["python3", "controllers/example/workloads/workload.py"]`. Python
workloads use the harness interpreter. Intentional shell programs are explicit
arguments such as `["bash", "script.sh"]`; ordinary commands are not interpreted
by a shell.

## Failure and resource ownership

Managed commands share an owned POSIX session with nested Sieve commands. On
failure/cancellation the owner signals the relevant groups, escalates if needed,
and reaps its direct children. Ctrl-C and SIGTERM trigger bounded cleanup and
exit with 130 and 143 respectively. SDK calls have socket limits and synchronous
wall-clock guards; request retries are disabled. These guards require the main
thread and reserve SIGALRM while a synchronous SDK operation is active.

Builds use BuildKit and label `docker run/create` helpers with a unique
`sieve.build` owner. Cleanup checks daemon-side containers independently of the
client process and removes only owned helpers. Build scripts configure Docker
routing through their environment/current context; global Docker options on
`run/create` are rejected because that routing cannot be tracked safely.

A host lock prevents overlapping Sieve experiments. A pre-existing `kind`
cluster is refused rather than replaced. The run records its cluster in
`resources.json`. Deployment assets are copied and rendered into the result
directory; tracked source manifests remain untouched. Available diagnostics are
collected before cluster deletion, including pod/PVC/workload-resource status
and namespace events. Cleanup failures are retained alongside the
original error.

An incomplete vanilla/learning workload stops the run before another baseline
or oracle generation. A test batch stops after a harness failure, incomplete
workload or workload timeout; it preserves the result and does not retry the
fault workload. Completed runs with oracle alarms remain available for bug
review. Process failures alone do not establish an operator bug.

For healthy RabbitMQ scaling validation, use `scaleup`. The pinned historical
operator rejects scale-down; `scaleup-scaledown` remains available as a historical
reproduction workload and is not a clean positive baseline.
