# Native remove/re-add pilot results

The bounded two-case pilot passed. Eight native workloads ran with ten-worker
settings: four fresh learning executions, two matched controls and two fault
executions. Both intended overlap placements were physically verified, and all
eight workloads passed independent semantic review. No previously unknown operator
defect was established.

| Operator | Learning executions | Matched control | Verified fault | Independent semantic passes |
| --- | --- | --- | --- | --- |
| External Secrets | 2 | Passed | Passed | 4 |
| cert-manager | 2 | Passed | Passed | 4 |

Both cases use `metadata-labels-remove-readd-at-2-overlap`: absent → one → absent →
two, with the generation-3 removal read held across full delivery of the
generation-4 re-add input. The frozen runtime is
`870c32c60e80e9eb701d6f5931ca09452de697d6`. Both learning pairs were independently
reviewed before controls started. Both matched controls passed before either
fault launched. Each test used its own copied, hash-bound fresh reference files.
Control and fault plans differ only in observe/hold mode.

Separate raw-marker review and Sieve's physical proof agree on both witnesses:
invocation begin, full old Get, held state, exact newer receipt begin/end,
release, invocation end and completion. Each witness binds parent UID, generation,
actual read resourceVersion, exact acknowledged re-add resourceVersion, native
reconciler, invocation and receiver identity. External Secrets held read RV 806
across delivery of RV 807; cert-manager held RV 747 across RV 750. The maximum
hold duration was configured as 20 seconds. This proves the selected observer
ordering, not same-key parallel reconciliation.

All four subject inputs were acknowledged and read, and all three updates were
fully delivered, in every run. Independent review re-read raw API replies and
objects; it verified latest accepted spec/generation, ownership, label `two`,
retained metadata entries, expected External Secrets data, and equivalence to a
freshly created reference. Cert-manager's actual TLS certificates, SAN, chain,
expiry and private-key match were checked with OpenSSL. Every final sampled
stability window exceeded 15 seconds in one Ready controller epoch. All eight
subject, fresh-reference and controller Pod UIDs were distinct across executions.
There were no controller restarts or deletions during the observed workloads.

The native pins remained External Secrets
`fc0dd82da6422c6ec840d24425bbda68e5259aab` with Go 1.27.3/client-go 0.37.1 and
cert-manager `925c22283de556172d48d96a165dc2fa27f7a6ff` with Go 1.26.10/client-go
0.36.2. Six controller/node image references were reverified before execution.
Existing independently verified images were reused. All 87 files under `go/`
match the verified test-build inputs, and both native ten-worker deployment
manifests are unchanged. Older learning and node images retain their separate
build provenance; this does not claim full current-Go-source identity for older
learning images. No images were rebuilt, retagged or pruned in this pilot.

The four test executions produced zero differential oracle alarms and zero
observer errors. Raw warning/error logs remain intact. Setup namespace and
transport messages were classified separately. Two Event-request timeout groups
were verified to precede operator Pod creation and the first accepted input;
their grouping is a timing/path-based inference, not an operator-defect verdict.
Cert-manager's optimistic-lock retries were classified only after later native
Ready/TLS/metadata recovery was independently verified. Streamed and collected
logs contain duplicates. Initial inventories, subsequent adjudications and blocked
review attempts are retained. A learning-gate receipt was corrected in a separate
v2 artifact to bind the alarm adjudication actually used; raw results and verdicts
were unchanged.

The accepted hard cap was ten workloads and three faults, including failures.
The pilot used eight workloads and two faults, with no automatic retries or
native workload failures. The remaining two-workload/one-fault pair was conditional
on a meaningful physically verified failure; none was established, so it remained
unused. The campaign is closed rather than spending the reserve on additional
coverage.

All six complete result trees were copied and compared with VM SHA256 manifests
before inspection: 3,894 files and links, 54,680,119 bytes. Frozen runtime, plans,
reference bindings, all raw evidence and review artifacts are retained in
`.cache/operator-sequences/history-placement-pilot-20261102/`. The complete idle VM
input directory was also copied and verified. All 83 prior result trees and eight
sealed phases were verified unchanged. All 130 preexisting image references still
point to their original IDs. The VM is idle. The protected test remains unchanged
and unstaged at SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
Nothing was pushed and no PR was opened.

The machine-readable campaign result is `final-review.json`; the explorer's own
`explorer-native-report.json` records two selected and paired verified placements
from four test executions. Learning executions are counted separately. This pilot
covers labels/remove-readd at the later edge, with one fault execution per
operator. Restore-value histories, annotations and earlier placements remain
unexecuted natively. Sampled bounded recovery does not establish continuous
correctness or the absence of other operator bugs.
