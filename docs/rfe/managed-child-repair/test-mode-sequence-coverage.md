# Test-mode sequence observations and coverage reporting

Test mode can now emit the invocation, successful scoped cache Get and informer
delivery records used by the existing overlap verifier. Observations are opt-in
through `coverageResources`, a list of at most sixteen unique exact
resource/namespace/name keys. Wildcards and global scopes are rejected by the
Python plan interface, coordinator and injected client configuration.

The instrumenter brackets annotated test-mode reconciliations with the same
goroutine-scoped invocation and receiver identities used in learning. It preserves
normal results, early returns and native defers. Scoped receipts are paired using
coordinator-assigned IDs; reads must belong to a live identified invocation.
One coordinator serializes these coverage records independently of fault trigger
notifications. Unscoped objects, missing/duplicate identities and mismatched ends
cannot supply valid coverage. Existing plans omit the option and emit no new
coverage records. Native operator logic, worker flags and scheduling actions are
unchanged. Observation RPC overhead is part of the enabled execution.

Public client/coordinator RPC contracts exercise ten concurrent workers, opt-in
and disabled plans, actual receipt pairing and rejection diagnostics. The public
instrumenter command generates, compiles and runs a controller fixture to check
normal and early returns. All three Go modules' race suites, modern client-go
contracts, Python public interfaces and the package checks validate this slice
locally. Native test-mode validation requires rebuilt test operator images and a
separately budgeted matched-control campaign; an old test image is insufficient.

## Coverage report

After collecting complete native result trees and verifying every file against
VM SHA256 manifests, create a separate derived report:

```sh
uv run --locked python -m sieve_tool.testing.sequence_report \
  --case /path/to/verified-case-one \
  --case /path/to/verified-case-two \
  --output /path/to/new-review/sequence-report.json
```

The output file must be new and its parent directory must exist. Inputs are
read-only. Each case binds its consumed artifacts by hash and records the
operator, schedule and selected history. Catalog histories are counted once per
operator; selected cases additionally distinguish schedules. Repeated executions
remain executions of the same case, and a repeated directory argument is not
counted twice.

The report separates:

- generated catalog histories and selected cases;
- actual attempts, including failed or incomplete attempts;
- complete acknowledged histories with declared input identity, generation,
  phase and metadata transitions;
- inputs read by identified reconciliations and exact completed input deliveries;
- complete input-read coverage and independently verified overlap witnesses;
- semantic passes reported by the workload/process artifacts.

Reported semantic success is separate from an independent semantic review and
from overlap proof. Metadata overlap intentionally allows an intermediate value
to go unread; its accepted, delivered and read coverage remain distinct. A
semantic failure does not erase a physically valid overlap witness. Observer
errors invalidate derived coverage. Missing artifacts and selected but unattempted
cases remain visible and prevent a satisfied aggregate.

The CLI's successful exit means a report was written. Read `status`, per-case
reasons and counts for the actual verdict; service completion and exit codes are
not experimental verdicts. A generated-only catalog has `not_attempted` status.
A satisfied report covers only its selected checks, with an overlap witness
required for attempted overlap schedules. It does not prove API acknowledgement
timing, same-key parallel processing, multi-parent concurrency, arbitrary
interleavings or absence of operator defects.

## Initial native scope

The authorized pilot has at most eight learning workload executions and zero
faults: External Secrets and cert-manager, label add/change/remove, settled and
overlap schedules, two fresh learning executions per case. It retains the exact
previously verified native learning images and ten-worker settings. New host
workload/monitor/report code and the coordinator source are frozen separately;
image reuse does not claim that new test-mode code ran inside an old image.
Annotation histories and native test-mode controls are outside this pilot.

No native result is assumed here. The campaign must retain failed attempts,
unreached overlap, raw alarms and complete evidence, and report semantic results
and physical coverage separately. Additional workloads or faults require a new
explicit budget.
