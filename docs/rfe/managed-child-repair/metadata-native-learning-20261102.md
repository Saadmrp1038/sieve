# Metadata native learning campaign — 2026-11-02

All eight fresh learning workloads passed independent semantic review. None of
the four executions requesting overlap produced the required delivery-during-old-
reconciliation witness. The campaign result is **semantic success with an overlap
coverage gap**. No operator defect was verified.

This completes the bounded campaign after the user delegated the explicit budget
choice: nine workloads total, including the preserved original harness failure,
zero faults, ten reconciliation workers. The budget is exhausted. No additional
execution or automatic retry was launched.

## Scope and results

The corrected host/coordinator/workload source was frozen at
`d8b7b403a0e3e142972b803b35af547fb188499d`, including the TDD JSON-output fix
`a17cc3d097d6b96068a1e7bf5cdb15810e3fab0f`. The snapshot's 734 files and six
symlinks were verified before execution. The selected history was label
add → change → remove, with a retained entry in each metadata map and a separately
created fresh parent/child pair for final-state comparison.

| Operator and schedule | Fresh executions | Independent semantic passes | All four inputs read | Required overlap witnesses |
| --- | ---: | ---: | ---: | ---: |
| External Secrets, settled | 2 | 2 | 2 | Not requested |
| External Secrets, overlap | 2 | 2 | 2 | 0 of 2 |
| cert-manager, settled | 2 | 2 | 2 | Not requested |
| cert-manager, overlap | 2 | 2 | 2 | 0 of 2 |

Each execution has four acknowledged subject inputs, all four read by identified
reconciliations, and all three exact updated inputs delivered through completed
full informer receipts. There are eight distinct subject UIDs, eight distinct
fresh-reference UIDs and eight distinct main-controller Pod UIDs. These are
repeated executions of **one history per operator**, selected under two schedules;
the 16 generated catalog histories were not all executed.

Independent review compared acknowledgement command stdout with recorded inputs
and API trace UID/resourceVersion/spec witnesses. It checked declared-only spec
changes, current-generation Ready state, parent-child ownership, actual Fake
provider Secret data or actual TLS material, retained metadata, removed-entry
cleanup and fresh-state equivalence. TLS validation covered the exact DNS SAN,
hostname, trust, server purpose, validity and key match. Successful final samples
span 15.48–15.83 seconds in one Ready main-controller epoch; the largest gap
between those samples was below 1.45 seconds. These are bounded sampled checks,
not proof of continuous or arbitrary-interleaving correctness.

Exact native image/imported digests and the arguments `--concurrent=10` or
`--concurrent-workers=10` were verified from API Pod evidence for every execution.
Each main controller had one Pod UID, zero container restarts and zero deletion
events. Worker settings were preserved; concurrency was not reduced to avoid
optimistic-lock retries.

The separately verified learning images were reused at the pinned versions:

- External Secrets: `fc0dd82da6422c6ec840d24425bbda68e5259aab`.
- cert-manager: `925c22283de556172d48d96a165dc2fa27f7a6ff`.

The registry remains `sieve-sequences-first-slice-retry3-20261101`. Native build,
compiler, SDK and imported-image provenance are bound in the frozen runtime and
copied native image review. This learning-only campaign **does not validate the
new test-mode observation code inside rebuilt native test images**. The public
RPC/instrumenter validation for that code is documented in
[the interface guide](test-mode-sequence-coverage.md).

## What the overlap gap establishes

The public coverage report and an independent tab/JSON marker reviewer agree:
there are no overlap witnesses. In each requested-overlap trace, invocations that
read older subject inputs had ended before the newer exact input's informer
receipt began. All invocation pairs completed, and no Sieve observer error was
found. The optional learning rate limiter was disabled in every frozen learn
plan and confirmed disabled by each server log.

Submitting the change and remove patches without waiting for intermediate
convergence did not achieve the intended interleaving. A schedule named
`overlap` is not evidence of overlap. These results do not establish API
acknowledgement timing relative to invocation start, same-key parallel
reconciliation, stale-input use after delivery, or safety under faults.

The next useful implementation target is a reproducible, explicitly verified
overlap schedule on these two operators, with rebuilt native test-mode observer
images and matched controls. The scheduling mechanism and any added delay must
be declared and separately budgeted. More executions of this unchanged schedule
would not resolve the demonstrated coverage gap reliably. No further run is
included in this completed campaign.

## Retained failures, alarms and evidence

The original failed External Secrets workload remains sealed in
`.cache/operator-sequences/test-mode-overlap-20261102`, with its full raw result
tree and [historical report](test-mode-sequence-coverage-20261102.md). Its missing
`-o json` arguments caused setup failure before any subject input was accepted.
The fixture was corrected to honor kubectl's default text output; RED was
reproduced independently for each adapter, followed by 42 passing metadata tests,
lint/format, strict adapter typing and a fresh wheel build. The initial observer
and report implementation had already passed 587 tests plus 10 subtests, all
three Go race/vet suites, modern SDK contracts and strict shared typing. No source
code changed during this native validation campaign.

Current evidence is in
`.cache/operator-sequences/test-mode-overlap-verified-20261102`:

- `final-review.json` separates semantic results, coverage and budget use.
- `corrected-eight-coverage.json` covers all eight fresh executions.
- `all-nine-coverage.json` additionally retains the failed harness attempt.
- Per-stage semantic/image reviews, overlap adjudications and alarm inventories
  retain first-pass unknown classifications and subsequent evidence-based reviews.

All four entire result trees were copied and checked against VM SHA256 manifests
**before inspection**: 3,952 files/links, 61,080,035 bytes. The original 100-file
failed tree remains unchanged. The entire remote campaign-input directory,
including the failed upload's duplicate copy, was also copied and verified:
46 files, 11,134,612 bytes. Frozen inputs, differential learning references,
generated but unexecuted fault plans, service journals, command logs and failed
preparations remain retained.

No differential test oracle was applied in learning mode. Retained log messages
include API-server bootstrap connection cancellations, early missing-namespace
and timeout messages, deliberate Fake provider maintenance warnings, and
cert-manager optimistic-lock requeues followed by independently verified
convergence. Six correlated bootstrap cancellation/HTTP-timeout messages ended
with a control-plane Event PATCH before the first workload input; their grouping
is a timing/path-based inference. Raw messages and both review stages remain
available. No selected alarm was discarded or promoted into an operator defect.

The preservation audit reverified all 70 older raw trees (310,173 files,
16,429,255,727 bytes), all four predecessor seals and their indexed contents, and
the protected test SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The protected file remains unchanged and unstaged. The VM is idle, and all 125
initially observed named image references retain their exact IDs. No credential
inspection, generative evaluation, local Docker, image pruning, push or PR was
performed.
