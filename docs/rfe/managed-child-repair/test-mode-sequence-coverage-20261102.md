# Test-mode sequence coverage slice — 2026-11-02

The opt-in test-mode observer and separate sequence coverage report are implemented
and validated locally. Native validation remains incomplete. The first learning
workload exposed a Sieve workload-adapter bug before accepting any subject input;
no operator correctness defect or delivery-overlap witness was established.

The implementation is on `codex/test-mode-sequence-coverage`, based on merged
main `9c07f1b8a78e7e625fe7ab729f16d31937c89700`:

- `2ca3cdaf8b46aa0586a35227cbb7c17c89ac5ca1`: scoped test-mode receipt/read/invocation
  observations and the bounded coverage report.
- `a17cc3d097d6b96068a1e7bf5cdb15810e3fab0f`: request JSON acknowledgements when
  creating metadata dependencies and parents; correct the external Kubernetes
  test fixture's default create-output behavior.

[The interface guide](test-mode-sequence-coverage.md) describes opt-in scopes,
identity checks, report counters and limits. Existing plans omit the observer.
Public RPC tests exercise ten concurrent workers; instrumenter tests compile and
run generated controller code, including early returns.

## Local validation

The initial implementation passed 587 Python tests plus 10 subtests, all three Go
race suites and vet checks, modern client-go contracts, lint/format, strict typing
of 44 shared sources and both adapters, and a wheel build. The protected test was
excluded. The later adapter fix was reproduced RED independently for each
operator through the workload CLI with external Kubernetes responses, then all
42 metadata workload tests passed. Lint/format, strict typing of each changed
adapter and a fresh wheel build passed after that fix. The full suite and Go
suites were not rerun for the Python adapter-only correction.

The corrected fixture returns kubectl's ordinary `kind/name created` text unless
JSON output is requested. Previously it always emitted JSON, hiding the missing
`-o json` arguments in both adapters. The four RED/GREEN temporary test trees
were copied in full and verified: 2,243 files and links.

## Native attempt and budget

The authorized campaign was eight learning workloads and zero faults: External
Secrets and cert-manager, labels add/change/remove, settled and overlap schedules,
two fresh executions per case, ten reconciliation workers. No automatic retries
were permitted. Only the first External Secrets settled stage launched.

| Measurement | Observed |
| --- | --- |
| Reserved workloads | 2 of 8 |
| Executed workloads, including failures | 1 |
| Fault attempts | 0 |
| Complete accepted subject histories | 0 |
| Independently verified semantic passes | 0 |
| Verified overlap executions | 0 |
| Other native stages | Unlaunched |

The real `kubectl create` of the Fake SecretStore succeeded and returned a text
message. JSON decoding failed during workload setup. The harness correctly
refused to learn from the incomplete baseline, so the second execution never
started and no differential references or fault plans were produced. This is a
harness failure, not evidence of an operator defect. The local fix has not yet
run on the VM.

The observed main controller used the exact verified learning image/imported
CRI digest, one Pod UID, zero container restarts, zero deletion events and the
`--concurrent=10` argument. Own-cluster cleanup completed. The VM was then idle;
all 125 initially observed named image references retained their exact IDs.

The new host source/coordinator was frozen at `2ca3cda`; the native learning
operator/node images were reused from their independently verified older build.
This does not validate the new test-mode code inside native operator images.
Rebuilding test images and matched test-mode controls remain outside this pilot.

## Evidence and limits

Evidence is retained under
`.cache/operator-sequences/test-mode-overlap-20261102`. The complete native tree
is `runs/operator-sequences-test-overlap-external-secrets-settled-20261102-vm`.
All 100 files/links (5,140,933 bytes) were copied before review and checked against
the VM SHA256 manifest, whose SHA256 is
`7dd9bc26e8cc0ff012a0936489aa4819b67a99982bb8f8d7ea6d57cc9c862e11`.
Raw workload/process failures, service journal, command receipts, alarms and
frozen inputs remain intact. The initial failed source-extraction preparation
and its partial VM snapshot are retained too; that preparation ran no workload.

`failed-stage-coverage.json` reports 16 generated histories across two operators,
four selected operator/schedule cases, one attempted execution and no complete
accepted/observed history. Generated catalogs and successful service completion
are not coverage or semantic passes. `external-secrets-settled-review.json`,
`failed-stage-classification.json` and `failed-stage-alarm-review.json` keep
physical checks and failure classifications separate. The latter retains 91
API-server local etcd connection-cancellation warnings from setup, one deliberate
Fake provider maintenance warning and two incomplete-baseline guard messages;
no Sieve observer error marker was found in the collected logs. These messages
do not establish application correctness.

The preservation audit reverified all 70 older raw trees (310,173 files,
16,429,255,727 bytes), all three sealed predecessor indexes and their contents,
and the protected test SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The protected file remains unstaged. No push or PR was performed.

Further native executions are awaiting an explicit budget choice. Completing
all four fresh pairs after the fix needs nine total workloads including this
failed one. Keeping the original eight-workload cap necessarily leaves a pair
incomplete. No extra workload or retry is authorized merely by this report, and
no frozen attempt will be altered or relabeled as passing.
