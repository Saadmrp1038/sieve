# Flux helm-controller failed upgrade reliability experiment

An operator defect was established in Flux helm-controller v1.7.1. A graceful
controller restart during an upgrade can leave a failed Helm release in Helm
storage without the same release in `HelmRelease.status.history`. The restarted
controller then clears the old status history and starts another upgrade. When
the second upgrade fails, rollback remediation has no previous status snapshot
to use, even though the previous deployed release still exists in Helm storage.

The defect reproduced in two fresh fault runs. Both matched controls completed
the same failed upgrade and rollback without a restart. The confirmation fault
used a physically verified controller restart, stayed in the same broken state
for 276 seconds, and failed three independent semantic checks.

| Category | Final result |
| --- | --- |
| Implementation | The helm-controller profile, synthetic workload, direct REST List fix, asynchronous restart plan, and failure analyzer are implemented. |
| Instrumentation | The test image reached the native Helm storage write for revision 2, and it kept the native `--concurrent=10` setting. |
| Matched controls | Both asynchronous controls rolled back to revision 1 and passed all five semantic checks. |
| Physical fault | Both asynchronous faults replaced the controller Pod. The confirmation changed the Pod name and UID, and the replacement became Ready with restart count zero. |
| Operator behavior | The fault left Helm storage revisions 1, 2, and 3, but status history contained only failed revision 3. Live resources stayed at revision 2. |
| Defect status | The failure was reproduced, physically verified, checked against current upstream source, and separated from harness failures. |

## Subject and expected behavior

The subject is Flux helm-controller v1.7.1 at commit
`da037f220ee3e97eb91a70506b5c5f169e46bb68`. The isolated controller watches
the default namespace and runs ten reconciliation workers with the native
`--concurrent=10` option.

The synthetic revision 1 chart creates a ConfigMap, Deployment, and Service
that return `revision-1`. Revision 2 changes the same resources to
`revision-2`, and it adds a post upgrade hook Job that fails. The HelmRelease
uses rollback remediation with zero retries and `remediateLastFailure: true`.

Without a restart, the failed revision 2 upgrade must roll back to revision 1.
The final Ready condition remains False because the requested revision failed,
but Helm storage, status history, live resources, and the Service response must
all agree that revision 1 is running.

## Implementation status

The campaign added three Sieve changes.

First, commit `0c83aae396b43372385e30a90a547794f4c53715` normalizes direct
Kubernetes REST List responses before sending them to the coordinator. An
empty List now becomes `[]`, and a nonempty List becomes its `items` array.
The public client and coordinator RPC regression test covers both shapes.

Second, commit `7ae2536` makes generated controller restart actions
asynchronous. The synchronous action made the native reconcile callback wait
for the restart RPC while the restart RPC waited for that controller process
to exit. The asynchronous action lets the callback return, so native graceful
shutdown can finish. The generation test failed before the change and passed
after it. Snapshot review confirmed that the only generated plan change was
`async: true` on the restart actions.

Third, commit `07a870b6c2da4bb9eaaebe82611e37a19ef38f74` preserves the
last workload observation when convergence times out. The public workload
analyzer now writes `workload.log`, `helm-upgrade.json`, and
`semantic-outcome.json` on a semantic failure. The new behavior test failed
before the implementation and passed after it.

The original local validation passed 733 tests and 10 subtests, with one test
skipped. Merge cleanup later passed 735 tests and 10 subtests. The protected
`tests/test_trace_errors.py` file was excluded. Ruff check, Ruff format
verification, strict mypy, all three Go race-test suites, wheel construction,
the local dependency audit, and `git diff --check` also passed. The focused VM
tests passed six tests. The qualified controller image remained
`sha256:c588c25b6639c2d4c70d20b7fca8ffd1b8d2fca46fd3347fc4148b81e0686649`.
The node image remained
`sha256:00cf9484987a342b4544499f3d2491a456bba1bf34c15665a17bd9eb8b78df7e`.

## Verified coverage

One vanilla run and two fresh learning runs established the ordinary behavior
and the scheduling point. Both learning traces recorded the first creation of
`Secret/default/sh.helm.release.v1.sieve-upgrade.v2` with Helm status
`pending-upgrade`. The write came from the pinned native HelmRelease
reconciler.

The frozen control plan has SHA256
`3ab738b6d8fe5298379ea207af5383ea32c33080c63e45e168d785becfe7fe1c`.
The fault plan has SHA256
`3d2021702bc8b890013531d4e2b3271a7abb58765a8679d4da3ecc692a711129`.
The plans use the same workload, images, oracles, and environment. The fault
plan adds one asynchronous restart after the exact revision 2 Secret creation.

The first asynchronous control completed with CLI status 0. It passed all five
semantic checks, and its controller Pod name, UID, and restart count did not
change. The first asynchronous fault completed the physical restart, but an
older failure path in the postprocessor expected a missing `workload.log` file
and raised `FileNotFoundError`. Raw evidence still showed the operator failure.
The analyzer correction removed that secondary harness failure before the
confirmation pair.

The confirmation control also completed with CLI status 0 and passed all five
checks. The controller Pod remained
`helm-controller-5f86dc85bf-nsp2k`, with UID
`8435625f-e937-4202-9a5c-93cdc6b8a71c` and restart count zero. Helm storage and
status history contained revision 1 as superseded, revision 2 as failed, and
revision 3 as the deployed rollback. The live resources and Service response
returned `revision-1`.

## Confirmed fault result

The confirmation trigger fired at 17:29:40 UTC after the revision 2 Secret was
created. Sieve started restart action 0, observed the old Pod disappear at
17:29:52, observed the replacement become Ready at 17:29:54, and completed the
restart action.

The old controller Pod was
`helm-controller-5f86dc85bf-dxcrc`, with UID
`4295cc2a-5274-45c7-a60b-ed43a95a0f0e`. The replacement was
`helm-controller-5f86dc85bf-xnt77`, with UID
`e86e0b24-f68a-410c-ae9a-0463692a7cc4`. The replacement was Ready and had a
restart count of zero. Independent API events recorded the old Pod deletion,
the new Pod addition, and the new Pod becoming Ready.

The workload observed the same final state in 141 of 157 samples. The state
was unchanged for 276.34 seconds, so the result was not ordinary asynchronous
convergence.

The final state was:

* Helm storage revision 1 was deployed and contained revision 1.
* Helm storage revision 2 was failed with `context canceled` and contained revision 2.
* Helm storage revision 3 was failed after the post upgrade hook and contained revision 2.
* `HelmRelease.status.history` contained only failed revision 3.
* The Stalled condition was True with reason `MissingRollbackTarget`.
* The live ConfigMap, Deployment, and Service response all contained revision 2.

The analyzer passed the initial consistency check and confirmed that the failed
upgrade was not reported Ready. It failed the rollback storage and status
check, the live resource and Service check, and the durable storage history
check. `fault-proof.json` reports `physically_verified: true` and
`semantic_status: failed`.

## Code path

The failure comes from a gap between Helm storage and HelmRelease status.

First, the upgrade writes revision 2 to Helm storage. The storage observer sees
the write, but `Upgrade.Reconcile` copies the observed releases into
`status.history` only after `action.Upgrade` returns. The controller shutdown
cancels that Helm action. Helm persists revision 2 as failed with
`context canceled`, but the matching status snapshot does not reach the API
server before the process exits.

Second, the replacement controller reads failed revision 2 from Helm storage
while status history still ends at revision 1. `DetermineReleaseState` calls
`VerifyReleaseObject`, which returns `ErrReleaseNotObserved` for the mismatch.
The state becomes `Unmanaged`.

Third, the `Unmanaged` branch in `AtomicRelease.actionForState` clears status
history and starts another upgrade. The second upgrade creates revision 3 and
fails because of the synthetic hook. The storage observer records revision 3
in the now empty status history.

Fourth, rollback remediation calls `Status.History.Previous`. The only status
snapshot is failed revision 3, so there is no previous deployed or superseded
snapshot. The controller sets `Stalled=True` with reason
`MissingRollbackTarget`. Revision 1 still exists in Helm storage, but rollback
intentionally uses controller observed status snapshots and will not select it
from storage alone.

Current upstream source contains an explicit cancellation patch in
`AtomicRelease.Reconcile`. Commit
`ac9c2c3142ddc436d9ad818fdb22a24e90201199` says that the controller should
persist the last observation during shutdown so it can recover from abrupt
termination. The experiment shows a shutdown window where that patch does not
preserve the revision 2 snapshot.

## Upstream review

Upstream main was reviewed at commit
`b7b9652a823ae6824b236a87b7e67a3e34ddb27a`. There is no diff between v1.7.1
and that commit in `internal/reconcile/atomic_release.go`,
`internal/reconcile/state.go`, `internal/reconcile/upgrade.go`, or
`api/v2/snapshot_types.go`. The same vulnerable state transition is therefore
present in current source. The original campaign did not build and run a
current main image. A later native validation is recorded below.

The status based state model and history tracking date back to the changes
released in v0.37.0. The cancellation patch was added in the same development
period. Source history does not prove the first affected release without
running older binaries, so the report does not claim a precise introduction
version.

The issue and pull request search found reports about missing rollback targets
for releases that Flux never observed, including flux2 issue 4772. That case is
different. In this experiment, the same HelmRelease installed revision 1, and
the controller lost its own observed history during shutdown. No exact report
for this restart path was found in the searched helm-controller issues and pull
requests.

## Follow-up validation on current main

On November 13, 2026, a separate bounded validation built and ran upstream
commit `b7b9652a823ae6824b236a87b7e67a3e34ddb27a` with the same native
`--concurrent=10` setting. The validation used two workload executions and one
scheduling fault, with no automatic retries.

The no-restart control passed all five semantic checks. Helm storage contained
superseded revision 1, failed revision 2, and deployed rollback revision 3. The
live Service returned `revision-1`.

The scheduled-restart execution physically replaced the controller Pod and
reproduced the failure. Helm storage contained deployed revision 1, failed
revision 2 with `context canceled`, and failed revision 3. Status history
contained only failed revision 3, the HelmRelease stalled with
`MissingRollbackTarget`, and the live Service continued to return `revision-2`.
The same three independent semantic checks failed as in the release validation.

The complete copied evidence is under
`.cache/helm-controller/main-restart-validation-20261113/`. VM SHA256 values
verified all 367 files in the control result and all 2,299 files in the fault
result before review.

## Harness and infrastructure failures

Harness failures were kept separate from operator results.

| Stage | Failure and result |
| --- | --- |
| VM setup and builds | Docker group access, a semver import alias, and a hook count assertion failed before qualification. Each cause was fixed before workload interpretation. |
| Early vanilla fixtures | A missing `lastTransitionTime` and an incomplete Service proxy path caused two workload failures. Fresh corrected runs were used. |
| First fault | Direct REST List returned a Kubernetes List object where the coordinator expected an array. The RPC boundary now normalizes the result. |
| Corrected fault | The synchronous restart waited on the reconcile callback that was waiting on the restart. Generated restart plans are now asynchronous. |
| First asynchronous fault | The operator failure occurred, but the postprocessor failed because the workload timeout path did not write `workload.log`. The public analyzer now preserves failure semantics. |
| Confirmation launch | A transient system service ran as root and Git rejected the user owned checkout. No workload or cluster started. The corrected service ran as the VM owner under a fresh result name. |

The confirmation launch failure did not consume a workload execution. Its
four file archive and VM manifest were copied and verified. No automatic
workload retry occurred.

## Execution accounting

The initial campaign used seven workload executions. It established the
vanilla behavior, two learning traces, a matched control, and the direct REST
List harness failure.

The continuation used seven workload executions and three scheduling fault
attempts:

| Execution | Mode | Result |
| --- | --- | --- |
| 1 | Control | User service stopped with the SSH session. |
| 2 | Control | Passed all semantic checks. |
| 3 | Fault | Reached the write, but the synchronous restart could not finish. |
| 4 | Asynchronous control | Passed all semantic checks. |
| 5 | Asynchronous fault | Physical restart completed and the operator failure occurred. The old analyzer then failed. |
| 6 | Confirmation control | Passed all semantic checks with the corrected analyzer. |
| 7 | Confirmation fault | Physical restart completed and the same operator failure was recorded cleanly. |

The extra root service launch failed before the runner could read the Git
revision. It did not create a result directory, increment the runner's workload
counter, start Sieve, or start Kubernetes.

## Evidence handling

All campaign stages used fresh result paths. While a stage was active, only
its completion marker and service state were inspected. Each completed VM tree
was copied in full and checked against VM SHA256 values before review.

The asynchronous control contains 390 verified files. Its manifest SHA256 is
`f9bb6f572c897a9d3607522b5afaaea0be376e7b5328a14cb1f23597cc3204d1`.
The first asynchronous fault contains 2,294 verified files. Its manifest
SHA256 is
`bab185c4d878af7287e15a4b0a51e78613e5d931a264df9baaf1ebeac5f982bd`.

The confirmation control contains 348 verified files. Its manifest SHA256 is
`74e4cce1a96ca97527630aae83db256682ed05c5badabb2e45c58b20c87ab151`.
The confirmation fault contains 2,294 verified files. Its manifest SHA256 is
`24f1239c1f6f991697baee4c75946eb3055ccb2a5e30a5e2fd410682b9aae65e`.
The prelaunch failure archive contains four verified files. Its manifest
SHA256 is
`67a5d1c52271204647f1b142f7bd345a5bfc943b6f28aedf13f58a29a39731c5`.

The original evidence root is
`.cache/helm-controller/upgrade-reliability-20261111/`. The correction,
asynchronous restart, and confirmation evidence root is
`.cache/rest-list-helm-restart-20261111/`. Both roots retain frozen inputs,
images, raw logs, alarms, failed attempts, result trees, and manifests.

The protected dirty file `tests/test_trace_errors.py` was not modified or
staged. Its SHA256 remains
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The five existing untracked Kustomize draft files were not modified, moved,
deleted, or staged. During the original campaign, no local Docker, image
pruning, credential inspection, generative evaluation, push, pull request, or
upstream publication occurred.
