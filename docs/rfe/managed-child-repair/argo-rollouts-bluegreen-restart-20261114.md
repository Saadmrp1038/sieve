# Argo Rollouts blue green restart experiment

## Status

The Sieve integration and validation are complete for Argo Rollouts v1.10.2 at
commit `be8a6221c6c9751cd14497f31a2cb3c33690375a`.

The campaign used seven workload executions and one scheduling fault. Two
early executions found harness defects. Two fresh learning passes, an
instrumented run without a restart, and a corrected unmodified run then passed
all seven semantic checks. The final run also passed after Sieve restarted the
controller.

The final run has a scheduling limit. Sieve started the restart action after
the controller stored the abort status, but Kubernetes deleted the old
controller Pod after it had already changed the active Service back to the
stable revision. The run proves recovery after a physical restart, but it does
not prove recovery from an interruption between the abort status write and the
Service write. No operator defect was established.

## Implementation status

The `argo-rollouts` profile builds vanilla, learn, and test images from the
pinned upstream source. It keeps the native v1.10.2 dependencies and worker
settings. The Rollout, Service, Experiment, and Ingress controllers each use
ten workers, and the Analysis controller uses its native default of thirty.

The deployment applies the pinned upstream CRDs and RBAC, then runs the isolated
controller in the default namespace. The workload uses synthetic Services, a
Rollout, and a deterministic Job analysis. It checks the Rollout status, both
Service selectors, ReplicaSet hashes, active EndpointSlices, AnalysisRun phase,
controller Pod identity, and an HTTP request through the active Service.

The change was developed with one failing public workload behavior test before
the implementation. The focused profile and package run passed 18 tests. The
full suite passed 736 tests and 10 subtests after excluding the protected test
file. Ruff, shell syntax, configuration loading, and manifest parsing checks
also passed. The protected `tests/test_trace_errors.py` file was not edited or
staged. Its SHA256 value remained
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.

A final local suite check passed 735 tests and 10 subtests, but one unrelated
CloudNativePG test used the system Python from `PATH` and could not import
PyYAML. The affected test passed when `PATH` included the repository virtual
environment. The Argo Rollouts test and Ruff check passed in the final check.

## Experiment behavior

Revision 1 serves `revision-1` through the active Service. After the workload
changes the Pod template, revision 2 serves `revision-2` through the same
Service. A post promotion AnalysisRun then starts a Job that waits eight
seconds and exits with status 1.

The expected fallback keeps the revision 1 ReplicaSet as `stableRS`. The
Rollout records the failed analysis and abort, then changes the active Service
selector back to revision 1. The workload accepts the final state only after
the status, selector, active EndpointSlice, selected ReplicaSet, and HTTP
response agree for five seconds.

## Verified coverage

| Execution | Image and purpose | Result |
| --- | --- | --- |
| 1 | Vanilla qualification | Harness failure before the controller started |
| 2 | First learning attempt | Harness failure during the first observation |
| 3 | Fresh learning pass 1 | Passed all seven semantic checks |
| 4 | Fresh learning pass 2 | Passed all seven semantic checks |
| 5 | Test image with no restart | Passed all Sieve oracles and all seven semantic checks |
| 6 | Corrected unmodified vanilla qualification | Passed all seven semantic checks |
| 7 | Test image with one scheduled restart | Physical restart completed, with zero Sieve oracle errors and all seven semantic checks passed |

The build qualification found exactly one before and one after reconciliation
hook in each instrumented mode. The vanilla source had no reconciliation hook.
The copied Sieve client matched the repository client file for file.

In both successful learning passes, revision 2 was reachable through the active
Service before the analysis failed. The final Rollout was degraded and aborted,
`stableRS` still identified revision 1, and `currentPodHash` identified the
failed revision 2. The status active selector, Service selector, EndpointSlice,
and HTTP response all returned to revision 1. The controller Pod did not change
during either run.

The test image control and the corrected unmodified run produced the same
result. The test image control reported zero Sieve oracle errors, no triggered
action, and no trace or RPC error. The unmodified run used the qualified
vanilla image and kept the same controller Pod UID with restart count zero.

The scheduled restart run also produced the expected final state. The active
Service selected revision 1, its EndpointSlice contained only revision 1, and
an HTTP request returned `revision-1`. The Rollout remained degraded and
aborted, `stableRS` identified revision 1, and the failed AnalysisRun remained
failed.

## Verified scheduling boundary

Both learning traces recorded the same controller write order. Rollout update
occurrence 13 stored `abort: true` and `phase: Degraded`. At that point,
`stableRS` identified revision 1, while `currentPodHash`, the Rollout status
active selector, and the active Service still identified revision 2.

The abort write was controller write index 21 in both traces. The active Service
rollback was write index 23 in both traces. The first trace used Rollout resource
version 752 for the abort write, and the second used resource version 754.

The frozen restart plan targets the completed abort write with an asynchronous
controller restart. The plan has SHA256
`de5db58b74fb9c82c778271b1686ca28ac3b38b2a0a89133cfb31e95c7c215a7`.

The final run triggered the restart at `22:21:04.229`, immediately after the
abort status write. The API log recorded the active Service rollback at
`22:21:04.318`. Kubernetes deleted the old controller Pod at `22:21:06.388`,
and the replacement Pod was ready at `22:21:08.523`. The workload observed the
old Pod UID `0f69410c-c2f8-4309-9fa2-2dc65db12736` and the replacement Pod UID
`cca2c077-2d92-41d9-a4a6-5258936b2931`.

The restart was physical, and Sieve recorded action 0 as triggered and
completed. However, the old controller completed the Service rollback before
its Pod was deleted. The final run therefore did not interrupt the narrow gap
that the learning traces identified.

## Harness failures

The first execution failed while applying the upstream resources. The deploy
script changed the ClusterRoleBinding ServiceAccount namespace, then joined the
following ConfigMap without a YAML document boundary. Kubernetes rejected the
ConfigMap before the controller started. The script now ends every transformed
document with a newline, and a local parser verified all 14 resulting objects.

The second execution started the controller and workload. The first observation
found a new EndpointSlice whose `endpoints` field was null. The workload treated
the field as a list and raised a `TypeError`. The observer now treats null API
list fields as empty lists. Both later learning passes and the test control used
that fix and completed successfully.

These are Sieve harness failures. They are not Argo Rollouts failures.

## Evidence integrity

The complete result tree for every stage was copied from the VM only after its
completion marker appeared. Every copied file matched the VM SHA256 manifest.

| Result tree | Files | Bytes | VM manifest SHA256 |
| --- | ---: | ---: | --- |
| Build qualification | 28 | 291,627,008 | `555afd4837d122b70be6dfb493874e348bc1568fd9c5441efcfbe26e523012e7` |
| Failed vanilla attempt | 45 | 6,275,072 | `507e10f269301853881999677134603ca3e10e972b073df68e65d9f18deb2b38` |
| Failed learning attempt | 106 | 12,038,144 | `806258e289e8d58a38da681a1e90e53573b59ba4511df09a52257200abf36a99` |
| Paired learning passes | 1,053 | 35,135,488 | `9d59891930cc9c54c34be5bbc687eb0143cf8df7d32c322a258a5a989db49c6d` |
| Test image control | 521 | 15,872,000 | `0fddab259848b595801cc8983046f6054dfcdd4cdb52d93543ae32ad1c3e33b9` |
| Corrected vanilla qualification | 503 | 14,217,216 | `f6b4a49acece65958c9b0ca62a37816d7d52bc38709b714535940bbdf6a5e88a` |
| Scheduled restart run | 541 | 16,842,752 | `277edc5b517d8494affa1e93dc33eb9a2d1c381843bb716a7578d7e74618ab96` |

The VM had no active Sieve service or running container after the final stage.
The campaign used seven workload executions, one scheduling fault attempt, and
zero automatic retries.

## Operator finding

The completed runs show the expected behavior with and without a controller
restart. Argo Rollouts returned traffic to the stable revision after the post
promotion analysis failed, and all independent checks agreed on the final
state.

The restart run did not test an interruption between the intended status and
Service writes, because the old controller finished the Service write before
Kubernetes deleted its Pod. The run cannot support a claim about that narrower
case. There is no independently established operator failure from this
campaign.
