# CloudNativePG Pooler replacement: bounded Sieve experiment

October 29, 2026 (America/Chicago; evidence timestamps are October 30 UTC).

The corrected vanilla control and both learning baselines passed. Three fault
attempts over two manually authored plans had independently verified effects
and passed all eight application checks. The paused old read **reproducibly
caused resources to be recreated for the deleted Pooler UID**, but garbage
collection removed those resources and the replacement converged successfully.
This establishes repeatable stale-owner resource churn, **not a demonstrated
application failure or a newly substantiated operator bug**.

The [sealed results](cloudnativepg-pooler-replacement-results.json) preserve every
raw alarm, accepted identity, physical fact, classification and evidence hash.
Their SHA256 is
`c54eb3baf3dffba5a62b7f2bf8e7414b48f7b7d4ea34924a4085514bee1b9ebe`.

## Workload and bounds

The separate `pooler-replacement` workload creates three PostgreSQL instances,
acknowledges 32 SQL rows and creates a healthy one-instance Pooler. A phase-B
annotation arms the fault. Ten seconds later, the workload deletes the old UID
with Background propagation, waits for its disappearance, and creates a Pooler
under the same name with a new UID and two instances. A UID-tested phase-C
update supplies the independent release condition.

Checks require the replacement UID/spec, correctly owned nonterminating children,
two Ready Deployment replicas, stability for 30 seconds after convergence,
unchanged Cluster identity/spec, all acknowledged rows on three instances and
one writable primary. Independent review also checks actual command outputs,
database Pod ownership/readiness and accepted API responses.

Each CLI phase has a 900-second limit. Control services have a 1,050-second cap;
learning and the two-plan service have 1,950-second caps. The 8 GiB floor and
default ten operator workers remain unchanged. Two ordinary plans and at most
two confirmation runs were allowed; one confirmation was used. There were no
automatic repeats or timer releases. The VM is idle after completion.

| Execution | Attempts | Outcome |
| --- | ---: | --- |
| Initial vanilla control | 1 | Preserved harness failure during final SQL selection |
| Corrected vanilla control | 1 | Passed independent application/native review |
| Fresh learning baselines | 2 | Both passed; 55 saved objects each match full API reconstruction |
| Hold old Pooler read across replacement | 2 | Supplemental physical audits verify both; all application checks passed |
| Restart at replacement phase C | 1 | API-proven restart; all application checks passed |

The two plans in `controllers/cloudnative-pg/plans/pooler-replacement/` are
manually authored hypothesis tests using existing Sieve actions. Learning also
generated 83 restart and 67 unobserved-state plans, and zero stale-state plans.
Those 150 generated plans are preserved, unexecuted and excluded from coverage.

## Physical proof and the observed difference

The first pause fetched old UID `7578b8eb-4fe2-488f-b7c6-76ed5f599e35`, RV `1306`.
Coordinator positions show the read at 89, hold at 91, old deletion receipt at 96,
new-object receipt at 104, exact replacement-C receipt at 112 and release at 122.
The new UID is `be83fbab-ab38-4e1f-8af8-b0545025a3c5`, with accepted C RV `1351`.
Independent API events confirm old-parent deletion at 388 and new-parent creation
at 399. Five direct children were then recreated for the **old UID**:
ServiceAccount, Role, RoleBinding, Deployment and Service. Each was actually
DELETED. Their ReplicaSet was also created and deleted; the replacement's own
children subsequently converged correctly.

The unchanged confirmation reproduced this with old UID
`89c7468f-304d-45e0-b857-6c606d3b883f` and new UID
`ead57fe7-2ee2-4b3e-8b31-c092e87f9a16`. Hold/deletion/new-C/release positions
were 94/99/115/125. The same five direct children and their ReplicaSet were
recreated; this time the stale ReplicaSet also created one short-lived Pod.
Every extra object was collected. The Pod's actual UID ancestry links it to the
stale ReplicaSet/Deployment and deleted parent.

The restart has an old-controller API DELETED event, a distinct new UID first
seen after deletion began, a final Ready nonterminating replacement, matching
action and completed injection process. It did not recreate old-owner children.

The generic proof supports before-read missed-state verification and retains
its **unverified after-read verdicts**. Separate strict audits verify actual
old-UID reads, accepted API identities, deletion/new-C receipts during the hold,
real release and successful injection completion. Negative audit controls reject
a wrong old UID and missing exact C receipt. The result distinguishes one
generic restart proof from two supplemental after-read verifications; it does
not claim stale-state-family coverage or alter raw verdicts.

## Interpretation and limits

Sieve exposed an old reconciliation acting on a snapshot fetched before deletion.
However, the extra resources retained the deleted owner's UID and were collected.
The final children belonged to the new UID and satisfied its requested spec.
No measured application invariant failed in either pause attempt or the restart.

The earlier upstream [deletion-timestamp fix](https://github.com/cloudnative-pg/cloudnative-pg/pull/11359)
addressed repeated recreation blocking external finalizers on terminating
parents. This Background-replacement experiment did not reproduce that blocking
failure. Dependent cleanup follows Kubernetes' documented
[ownership behavior](https://kubernetes.io/docs/concepts/architecture/garbage-collection/).

This does not prove every ownership race is safe. Scope is one kind node, one
replacement, the selected timings and a 30-second stability window. SQL checks
connect directly to PostgreSQL; Pooler-client availability during replacement
was not measured. No persistent wrong ownership, corruption or nonconvergence
was demonstrated. No upstream issue or operator patch was created.

## Alarms and preservation

All three raw test CLI invocations returned 1. All **57 alarms** are retained:

| Classification | Alarms |
| --- | ---: |
| Learn/test hashes matching their actual built binaries | 6 |
| Cross-cluster Secret RV references matching actual API metadata | 24 |
| Extra old-owner resource/descendant creation and deletion cycles | 26 |
| Actual graceful Lease release and new leader, counter 0 to 1 | 1 |

The Lease retained its UID and followed old / empty / new holder, with counters
0/0/1. The empty state occurred during old-controller deletion, before its
DELETED event, with a one-second duration. This matches pinned client-go v0.37.0
[graceful release](https://github.com/kubernetes/client-go/blob/v0.37.0/tools/leaderelection/leaderelection.go).
No oracle, alarm, prior plan or raw verdict was changed.

Every terminal tree was copied and SHA256-verified before inspection. Final
aggregation reverified **55,639 files / 2,086,813,466 bytes** across five trees.
All 66 pre-existing preservation hashes still match, including old reports,
campaign manifests/state, previous current-job metadata and the dirty user test.

Evidence roots are `runs/cloudnativepg-replacement-*-vm`. Jobs, manifests, initial
failed/manual reviews, final reviews, runtime archives and audit scripts remain
under `.cache/cloudnativepg/pooler-replacement-20261029/`. An initial API audit
expected kubectl to display `managedFields`; its failed review is preserved.
The fresh audit compares all displayed response fields and independently checks
the complete saved state against API reconstruction without masking fields.

## Implementation and validation

Local commit `377f56a` adds the separate workload and plans. Its public CLI
success regression failed before implementation and passed afterward; synthetic
negative controls reject stale ownership and lost acknowledged rows.

The first VM control exposed a harness mistake: the Cluster label also selected
Pooler pods, so final SQL tried the `postgres` container in a PgBouncer pod.
That run has no complete semantic outcome and remains a harness failure.
Commit `ad86f0d` filters by the current Cluster controller UID, with a public
boundary regression RED before the fix and GREEN afterward.

The corrected runtime has 659 tracked files and six links, archive SHA256
`ea82ec34d068af3f8a757013d77a919f2891a9ff1796b3359ee0958909bc8d2f`.
It reuses native subject `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`, existing
controller/API/operand images and their provenance. Fixed vanilla/test images
retain all 1,002 original Go files and default concurrency.

Validation passed **404 Python tests plus 10 subtests**, including 21
CloudNativePG workload tests, Ruff lint/format excluding the untouched user edit,
configured strict Mypy plus both workload modules, and wheel build. No generative
evaluation, credential access, evidence pruning, controller/API rebuild, CI
change, push or PR update was performed. `tests/test_trace_errors.py` remains
untouched and unstaged.
