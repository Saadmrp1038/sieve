# Reproducible overlap: verified native results

Both rebuilt test-mode operator images passed fresh matched controls and two
independent scheduled overlap executions. Six workloads and four timing fault
attempts ran with ten-worker settings and no automatic retries. All six workload
semantic outcomes passed. No previously unknown operator defect was established.

| Operator | Native pin | Native compiler | Matched controls | Verified overlaps | Semantic passes |
| --- | --- | --- | --- | --- | --- |
| external-secrets | `fc0dd82da642` | go1.27.3 | 1 | 2/2 | 3 |
| cert-manager | `925c22283de5` | go1.26.10 | 1 | 2/2 | 3 |

The shared protocol is committed in `00bd12209f9257f3a00018f887a19836f5b541bb`.
The build-tool routing fix and public build-CLI regression are committed in
`9a3fb874f021b2ad8337543ae09d2b055d05ca83`. Test workloads used the same frozen
runtime and per-operator image for controls and holds. The build-only fix leaves
all 87 files under `go/` (including 56 `.go` source files) and native runtime
assets byte-identical. Control and
hold plans differ only in mode; the two hold plans for each operator are identical.
All six runs have distinct subject, fresh-reference and controller Pod UIDs.

Each hold has an independently checked native witness: invocation begin, successful
full generation-3 Get, held state, exact full generation-4 receipt begin/end,
release, invocation end and completion. It binds the resource UID, native frame,
invocation, receiver instance, actual read resourceVersion and exact acknowledged
removal resourceVersion. Both the public fault-proof implementation and separate
raw-marker review agree. The hold deadline is 20 seconds. Other workers remain
available by the public ten-worker RPC contract; each native Pod retained its
explicit ten-worker argument, with zero controller restarts or deletions.

All four subject inputs were acknowledged and observed, and all three complete
updates were delivered, in every run. Independent review re-read raw API replies
and objects, verified child ownership and metadata cleanup, and compared the
final history result with a freshly created reference. External Secrets retained
its expected secret data. Cert-manager's collected TLS certificates, SANs, chain,
expiry and private-key matches were checked with OpenSSL. Each final sampled
stability window exceeded 15 seconds in one Ready controller epoch. Intermediate
change-input convergence is intentionally not required for the overlap schedule.

Independent image review accounted for 812 original External Secrets Go files
and 927 cert-manager files. Original operator logic was preserved; only
approved observation wrappers and imports changed the native Go sources. All 368 and 179 native module versions, respectively, were
preserved; the packaged binaries match the native artifacts. External Secrets
uses client-go 0.37.1 and controller-runtime 0.25.2; cert-manager uses 0.36.2 and
0.24.1. The independently verified older test node image and learning references
were reused with explicit frozen provenance; these are newly rebuilt controller
images, not a claim of a newly rebuilt API-server image.

There were zero differential oracle alarms and zero observer errors. Raw logs
remain intact: setup transport/namespace warnings and the intentional Fake provider
warning are classified separately. Cert-manager optimistic-lock requeues were
adjudicated only after later independent Ready/TLS/metadata recovery was verified;
the log inventory includes duplicates from streamed and collected controller logs.
Successful eventual recovery does not establish uninterrupted availability.

The first native build stage failed when cert-manager's pinned Go 1.26.10 was
inherited by Sieve's instrumenter, which requires Go 1.27. That failure and all
receipts remain preserved. The new regression first failed through the public
image-build CLI with external compiler/daemon fixtures. The minimal fix selects
`GOTOOLCHAIN=auto` only for the instrumenter build subprocess. The fixture image
still uses the native compiler pin, and the real rebuilt cert-manager binary was
independently verified as Go 1.26.10. The related 33 build/package/process tests,
changed-file lint/format, strict checks for 45 shared Python sources and wheel
build passed. The preceding full suite passed 609 Python tests plus 10 subtests,
and all three unchanged Go modules passed race tests and vet. The protected test
was excluded from execution, formatting and staging.

All complete VM result trees were copied and compared file-for-file with VM
SHA256 manifests before inspection. Both build trees, all six workload trees,
all raw alarms, failed helper receipts, TDD logs and frozen inputs are retained.
The entire idle VM input directory was also copied and verified. All 75 older
result trees, five predecessor evidence seals and the sealed External Secrets
checkpoint remain unchanged. All 125 preexisting image references retain their
original IDs; no images were pruned. The VM is idle. The protected test remains
at SHA256 `b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
Nothing was pushed and no PR was opened.

This closes the earlier overlap coverage gap for one metadata label
add/change/remove history per operator. Generated catalogs are not executed
histories. The witnesses prove one invocation spanning full input delivery, not
same-key parallel reconciliation. Sampled bounded semantics do not prove
continuous correctness or arbitrary interleavings. Four successful fault
executions establish reproducible coverage for these selected schedules, not the
absence of operator bugs.

Machine-readable results, exact hashes, plans and independent reviews are in
`.cache/operator-sequences/reproducible-overlap-20261102/final-review-v2.json` and the
surrounding evidence directory. The earlier partial checkpoint is retained as
historical evidence; this report records the completed native validation.
