# Bounded operator sequences: implementation and six-workload validation

The implementation is committed locally in `09cd60a`, `11becfd` and `9a7c8be`.
**The requested physical fault validation across both operators remains incomplete.**
The six-workload cap was respected: five workloads passed, one failed in the
harness, and zero faults launched. No operator defect is established by this slice.
There was no push or PR.

The shared generator enumerates short histories, including reversions, with hard
limits of three updates and sixteen candidates. The default a/b/c catalog has
fourteen histories; one selected history, a→b→c→a, was exercised for each operator.
External Secrets changes Fake-provider versions v1→v2→v3→v1. cert-manager changes
one Certificate's declared DNS name and returns to the initial name. Each profile
uses one parent key and explicitly configures ten reconciliation workers.
Kubernetes `num_workers: 0` is a node-count setting. This does not exercise ten
simultaneously active parent keys or every generated history/interleaving.

Reusable monitors check parent UID, accepted generation/spec, exact controller
ownership, current-generation Ready and actual effective child value. cert-manager
also checks actual TLS validity, hostname, SAN and matching public keys. Ready
with stale data is retained as a violation even if later reconciliation heals.
Pending readiness is tolerated within a bounded wait. Final stability requires
fifteen seconds of satisfied samples with the same Ready controller Pod UID;
pending observations or Pod replacement reset the window. Sampling does not
exclude failures between observations.

`--plan-only` emits the catalog and matched no-fault/restart YAMLs without
Kubernetes access. Normal execution preserves Sieve's copied execution YAML and
writes the catalog, accepted responses, expected-input/timing bindings, full
observations, semantic verdicts and raw command journals. Existing output
artifacts are rejected. Saved fault proofs bind the sequence evidence by SHA256
and retain raw alarms separately from semantic and physical verdicts.

The shared fault family is one asynchronous `restartController` after informer
receipt of the first labeled update (`sequence-1-b`). It needs actual receipt,
old-Pod API deletion, a distinct Ready replacement, unchanged lineage/image and
ten workers before/after to establish effectiveness. No such new fault was
executed here. The initial after-native-write state-match proposal was rejected
by the supported schema; the changed anchor and failed attempt are retained.

| Operator | Fresh workload | Result | Final sampled stability |
| --- | --- | --- | --- |
| External Secrets | Learning baseline 1 | Passed | 15.494 s |
| External Secrets | Learning baseline 2 | Passed | 15.325 s |
| External Secrets | Matched test-image control | Harness failure before sequence setup | Not established |
| cert-manager | Learning baseline 1 | Passed | 15.546 s |
| cert-manager | Learning baseline 2 | Passed | 15.620 s |
| cert-manager | Matched test-image control | Passed; zero raw alarms | 15.439 s |

Independent review matched all twenty accepted input snapshots in the five
successful histories to API-server events. The greatest final-stability sample
gap was 0.874 seconds. Pending samples represented unconfirmed current readiness;
no observed semantic violation occurred in those successful runs. All six
infrastructure runs used the expected imported image bytes and ten-worker
arguments, with one controller Pod UID, zero container restarts and no Pod API
deletion. These clean controls are not evidence of an injected restart.

The failed External Secrets control exposed an integration collision: Sieve
copies its execution YAML into the result directory, and the new workload's
plan-emission/output guard rejected that input filename. The process exited 2
before creating sequence evidence. Oracle postprocessing then recorded a missing
workload-log exception. Public CLI tests first reproduced the collision; commit
`9a7c8be` makes YAML generation exclusive to plan-only mode and preserves copied
input plans during normal execution. cert-manager's subsequent matched control
passed on that corrected host harness. External Secrets still needs a repaired
matched control before any fault; its two independently verified learning runs
remain retained and reusable because only local artifact handling changed.

The full suite passes **492 tests plus 10 subtests**, excluding the protected
preexisting dirty test. Lint/format checks, strict typing for 41 shared sources
and both adapters, and wheel build passed. Detector fixtures cover stale Ready
data/certificates, mismatched keys, rejected or unaccepted input, nonconvergence,
worker-count errors, unavailable controllers, evidence changes, output reuse and
copied input plans. These fixtures validate detectors and are not operator bugs.

External Secrets remains pinned at `fc0dd82da6422c6ec840d24425bbda68e5259aab`
with native Go1.27.3, client-go0.37.1 and controller-runtime0.25.2. cert-manager
remains pinned at `925c22283de556172d48d96a165dc2fa27f7a6ff` with native
Go1.26.10, client-go0.36.2 and controller-runtime0.24.1. Original Go blobs
(812/927 files), native module versions (368/179), compilers and packaged OCI
binaries were independently checked. Test-mode original operator Go files are
unchanged; learning adds observation calls and reorders imports. Reconciliation
rules and provider registrations were preserved. API/node images match the
verified current shared Go sources and exact IDs. Unique image tags preserve
all older references.

All setup failures remain available: three isolated build-driver failures
(branch collision, oversized provenance capture, compiler routing), the workload
collision, and reviewer corrections for image symlinks, YAML loading and CRI
single-image export-index identity. They are classified as harness/review
failures. Every terminal result tree was copied in full and every file checked
against VM SHA256 values before inspection. No live result inspection occurred;
only service state and DONE were read during stages.

Final audit verified all sixty prior trees unchanged: 306,352 files and
14,674,913,176 bytes. All seven new result trees were reverified: 2,631 files and
1,733,907,748 bytes. The protected test remains unstaged with SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The VM is idle with no containers or Kind network attachments; all 108 prior
named image references retain their IDs. Older campaigns/reports remain intact.
There was no credential inspection/copying, generative evaluation, local Docker
use or image pruning.

Completing the requested family verification now needs **nine total workloads
and at most two faults**, counting the failed attempt: one repaired External
Secrets matched control, then one gated restart per operator. The explicit
budget question remains unanswered; a preselected choice is not authorization.
The launcher was verified to reject workload 7 before job creation or SSH.
The separate, closed FailoverQuorum campaign's allowance is not reused.

The machine-readable companion is
[operator-sequences-first-slice-bounded-results.json](operator-sequences-first-slice-bounded-results.json).
Raw evidence, TDD logs, complete VM manifests, independent reviews and frozen
inputs are under `.cache/operator-sequences/first-slice-20261101/`. Resume from
`CONTINUATION-CAP6.md`; preserve this phase's report and evidence when adding any
subsequently authorized fault stage.
