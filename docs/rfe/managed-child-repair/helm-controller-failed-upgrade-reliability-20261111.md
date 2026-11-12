# Flux helm-controller failed-upgrade reliability experiment

No helm-controller defect was established. The unmodified controller, two
learning workloads, and both matched test-image controls completed the
configured failed-upgrade rollback correctly. The direct REST List contract
was fixed and the rerun reached the selected write. Sieve then requested the
controller restart, but its synchronous restart action waited on the same
controller process that was waiting for the action to return. The restart did
not finish before the workload timeout. The interrupted recovery hypothesis
therefore remains untested.

| Category | Established result |
| --- | --- |
| Implementation | The helm-controller profile, native build and deployment assets, synthetic chart workload, independent semantic checks, and direct REST List correction are present. |
| Instrumentation | Two fresh learning workloads observed the same pending-upgrade Secret creation from the native HelmRelease reconciler. |
| Matched controls | Both test-image controls completed the failed upgrade and rollback with no action, no raw alarm, and all semantic checks passing. |
| Scheduling | The rerun reached the target write and Kubernetes accepted deletion of the controller Deployment. The old Pod entered graceful termination, but no replacement became ready before timeout. |
| Operator behavior | Every completed workload returned storage, HelmRelease history, live resources, and the Service response to revision 1. |
| Defect status | No operator defect or operator anomaly is claimed. Both fault attempts ended in separate Sieve harness failures. |

## Implementation status

The profile pins Flux helm-controller v1.7.1 at commit
`da037f220ee3e97eb91a70506b5c5f169e46bb68`. It retains the upstream Go 1.27
toolchain, Helm v4.3.1, client-go v0.37.1, controller-runtime v0.25.2, build
flags, controller logic, and runtime packages. The isolated controller watches
the default namespace and uses the native `--concurrent=10` option.

The synthetic revision 1 chart creates a ConfigMap, Deployment, and Service
that serve `revision-1`. Revision 2 changes the same resources to
`revision-2` and adds a post-upgrade hook Job that exits with status 1. The
HelmRelease uses `RemediateOnFailure`, zero retries, rollback remediation, and
`remediateLastFailure: true`.

The workload records HelmRelease conditions and history, decoded metadata from
the synthetic Helm storage Secrets, live Deployment and ConfigMap state,
controller and workload Pod identities, and responses through the Kubernetes
Service proxy. It saves the accepted API responses and observations before it
writes its semantic result.

Test-driven development covered two public behaviors. The first failing test
showed that the public controller loader had no helm-controller profile. The
second showed that the public workload entry point did not exist. The passing
tests prove that the profile exposes the pinned release and native reconcile
function, and that the workload rejects a controller that is not using ten
workers before it mutates a cluster.

The instrumenter initially removed the semver v3 import because it inferred the
wrong package name. A real Go build test reproduced the failure. The
instrumenter now maps `github.com/Masterminds/semver/v3` to package `semver`,
and the regression test compiles a synthetic module through the public Sieve
build path.

The final qualified image set contains zero controller hooks in vanilla mode
and one matching before and after hook in both learn and test modes. The final
controller image identifiers are preserved in the campaign evidence.

Final local validation passed 732 tests and 10 subtests, with one
environment-dependent test skipped and `tests/test_trace_errors.py` excluded.
The focused helm-controller and import-alias run passed two tests and skipped
the same environment-dependent real-Go case. The preserved VM real-Go test
passed. Ruff check, Ruff format verification, strict mypy for the workload,
shell syntax checks, and `git diff --check` also passed.

## Verified instrumentation and workload coverage

One unmodified vanilla workload completed successfully. Two fresh learning
workloads then completed successfully and produced separate traces. Both
learning traces recorded write event 24 as the first creation of
`Secret/default/sh.helm.release.v1.sieve-upgrade.v2` by
`github.com/fluxcd/helm-controller/internal/controller.(*HelmReleaseReconciler).Reconcile`.
The Secret had status `pending-upgrade`. The recorded resource versions were
680 and 681.

The learning evidence changed the scheduling hypothesis. The stable observed
boundary was creation of the pending-upgrade v2 Secret, not the later update
that records the failed release. The frozen plan therefore schedules a restart
after the pending Secret creation. Its SHA256 is
`6daa07b18840579ff9db9952fae95c056a89b37abcebbd5bfa1be9528aeed847`.

The matched test-image control used the same test image, workload, oracles, and
environment as the fault attempt. Its action list was empty. The control
completed with CLI status 0, no triggered action, no raw alarm, and all five
independent semantic checks passing.

## Harness and infrastructure failures

The campaign preserved each failure instead of replacing its output.

| Stage | Failure and disposition |
| --- | --- |
| VM preparation | The VM user had lost access to the Docker group. Membership was restored before any build or workload started. |
| First build | The learn image failed to compile because the instrumenter removed the semver v3 import. The package-name mapping and real Go regression test fixed the cause. |
| Second build | The external build runner counted a test hook while qualifying the learn image and failed its assertion. The runner was corrected, and the third build qualified all three images. |
| First vanilla workload | The synthetic HelmChart status omitted the required `lastTransitionTime`. Kubernetes rejected the fixture before the experiment. |
| Second vanilla workload | The Service proxy path treated `http` as the Service name and repeatedly returned `services "http" not found`. The complete proxy name fixed the fixture. |
| Fault workload | The Sieve test coordinator could not parse an empty Secret list returned through the direct REST instrumentation path. No scheduling action occurred. |

The failed build and transfer outputs, partial transfers, command logs, inputs,
and final images remain in the campaign cache. Build failures did not count as
workload executions. The two failed vanilla fixtures did count because their
native workloads had started.

## Original scheduling attempt and physical evidence

The fault workload started with the qualified test image and the frozen restart
plan. The workload itself completed, but the coordinator exited before the
target Secret was created.

At 03:44:02 UTC, helm-controller performed a direct REST list of Helm storage
Secrets before any release existed. Kubernetes returned a valid List object:

```json
{"metadata":{"resourceVersion":"613"},"items":null}
```

`NotifyTestAfterRestCall` sent that complete object as `ObjectList`. The test
coordinator passed the value to `strToSlice`, which accepts only a bare JSON
array. `strToSlice` called `log.Fatalf`, and the coordinator process exited.
Later controller callbacks reported `Sieve client RPC error: connection is shut
down`.

The fault proof reports no triggered action, no matching deletion of the old
controller Pod, and no distinct ready replacement Pod. The injection subprocess
returned 1, and physical verification is false. The CLI returned 1 because the
scheduling action was incomplete. The run does not contain evidence of a
controller restart.

The parser failure is a Sieve harness problem. It happened before the selected
operator boundary and cannot support any conclusion about helm-controller's
behavior after interruption.

## Direct REST List correction

The rerun used Sieve commit
`0c83aae396b43372385e30a90a547794f4c53715`. The Go client now normalizes the
Kubernetes List response before it calls the coordinator. A List with
`items: null` becomes an empty JSON array. A nonempty List becomes the JSON
array from its `items` field. The coordinator therefore receives the same
shape for direct REST lists that it already receives for client-go lists.

The regression test uses the public client and coordinator RPC contracts. It
checks an empty direct REST list, a nonempty list, and the later creation of the
pending-upgrade v2 Secret. The test failed before the implementation and passed
after it. The corrected image also logged the direct Secret list as a bare
array during the native fault run. The previous `items: null` crash did not
recur.

The corrected controller test image has ID
`sha256:c588c25b6639c2d4c70d20b7fca8ffd1b8d2fca46fd3347fc4148b81e0686649`.
The node image has ID
`sha256:00cf9484987a342b4544499f3d2491a456bba1bf34c15665a17bd9eb8b78df7e`.
The image qualification retained the pinned helm-controller commit and its
native `--concurrent=10` setting.

Local validation passed 732 Python tests, with one environment-dependent test
skipped and the protected test excluded. Ruff check, Ruff format verification,
strict mypy, CLI loading, the VM Go race tests, Go vet, and gofmt verification
also passed.

## Corrected-image matched control

The first launch of the corrected control used a transient user service. The
VM has user lingering disabled, so its user service manager stopped when the
SSH session closed. It sent SIGTERM while kind was being created. This was an
infrastructure failure and consumed one workload execution.

The replacement control used a persistent system service. It completed with
CLI status 0, no scheduling action, no alarm, and all five independent semantic
checks passing. Its initial controller Pod remained in place with the same
name and UID and a restart count of zero.

The final HelmRelease Ready condition was False with reason
`RollbackSucceeded`. HelmRelease history and Helm storage both recorded the
failed revision 2 and the deployed rollback revision 3 with revision 1
content. The live Deployment template and ConfigMap contained revision 1, and
the Service returned revision 1. This established the expected result for the
same image, plan inputs, workload, and environment used by the fault run.

## Corrected-image fault and physical evidence

The fault run reached the selected event. At 05:36:25 UTC, helm-controller
created `Secret/default/sh.helm.release.v1.sieve-upgrade.v2` with status
`pending-upgrade`. The callback came from the pinned native HelmRelease
reconciler. The Sieve server recorded that the trigger was satisfied and began
restart action 0.

Kubernetes accepted deletion of the controller Deployment. The old controller
Pod was
`helm-controller-5f86dc85bf-wq52c`, with UID
`5e5b67b3-529b-443b-9632-a4b1f43a61d5`. Its deletion grace period and
termination grace period were both 600 seconds. The saved Pod had a deletion
deadline ten minutes after the trigger. It was still Running and Ready while
terminating. No distinct replacement Pod became ready before the workload
timed out.

The restart action did not complete. The fault proof is therefore physically
unverified, the injection process timed out, and the workload could not run its
post-restart semantic checks. The result contains no operator verdict.

## Rerun harness failure

The failure comes from the interaction between the synchronous restart action
and graceful controller shutdown. `restartController` in
`go/coordinator/controller_actions.go` deletes the controller Deployment with
the default deletion options. It then waits synchronously until the selected
controller Pods are gone.

The controller received SIGTERM and began waiting for its workers to finish.
One worker was handling the reconcile that made the Sieve callback. That
callback was still waiting for the coordinator's restart RPC to return. The
coordinator was waiting for the old controller Pod to finish terminating.
Kubernetes allowed 600 seconds for graceful termination, while the workload
allowed 300 seconds for convergence.

This formed a wait cycle:

```text
controller reconcile waits for the restart RPC
coordinator restart RPC waits for the old Pod to disappear
old controller process waits for the reconcile worker to finish
```

The result is a Sieve restart harness failure. It does not show how
helm-controller behaves after a completed restart. A new test-driven harness
change and another matched control and fault pair are required before the
operator hypothesis can be evaluated.

## Independent semantic result

The successful vanilla workload, both learning workloads, the matched control,
and the fault workload without an injected action all reached the same stable
state:

- Helm storage revision 1 was `superseded` and contained revision 1.
- Helm storage revision 2 was `failed` and contained revision 2.
- Helm storage revision 3 was `deployed` and contained the rollback to revision 1.
- HelmRelease history contained the same three revisions and states.
- The current-generation Ready condition was False with reason `RollbackSucceeded`.
- The live ConfigMap and Deployment template described revision 1, and the
  Service response returned revision 1. The evidence also records the active
  and terminating Pod identities.

All five semantic checks passed in each completed workload. The checks cover
initial agreement, a failed upgrade that is not reported Ready, agreement
between rollback storage and status, agreement between live resources and the
Service response, and inclusion of every durable storage revision in status
history.

The fault workload's semantic success describes ordinary uninterrupted
remediation because Sieve did not restart the controller. It is not evidence
that restart recovery works.

## Operator behavior and upstream history

The subject release is
[helm-controller v1.7.1](https://github.com/fluxcd/helm-controller/tree/da037f220ee3e97eb91a70506b5c5f169e46bb68).
The source review followed install, upgrade, rollback, remediation, status, and
Helm storage paths. It also reviewed the pending-release state machine from
[pull request 738](https://github.com/fluxcd/helm-controller/pull/738), status
repair from [pull request 1580](https://github.com/fluxcd/helm-controller/pull/1580),
and the pre-storage failure correction from
[pull request 1640](https://github.com/fluxcd/helm-controller/pull/1640).
The review also considered the pending-release lock reported in
[issue 644](https://github.com/fluxcd/helm-controller/issues/644) and the
ownership and adoption behavior discussed in
[flux2 issue 4772](https://github.com/fluxcd/flux2/issues/4772).

Upstream main was reviewed at commit
`b7b9652a823ae6824b236a87b7e67a3e34ddb27a`. No change between v1.7.1 and that
commit modified the upgrade, rollback, storage observer, release state, or
status patch paths used by the workload.

The completed workloads show that v1.7.1 follows its configured remediation
policy when reconciliation is uninterrupted. No physically scheduled result is
available. The source and issue review did not identify a basis for changing the
expected semantics, but the campaign did not reach the threshold for a native
current-main reproduction or a previously unknown operator defect claim.

## Execution budgets and remaining limit

The campaign used all seven permitted workload executions:

| Execution | Mode | Result |
| --- | --- | --- |
| 1 | Vanilla | Fixture failure because HelmChart status lacked `lastTransitionTime`. |
| 2 | Vanilla | Fixture failure because the Service proxy name was incomplete. |
| 3 | Vanilla | Passed all semantic checks. |
| 4 | Learn | Passed and recorded the pending-upgrade Secret creation. |
| 5 | Learn | Passed and recorded the same creation boundary. |
| 6 | Test control | Passed with no action and no raw alarm. |
| 7 | Test fault | Workload semantics passed, but the coordinator exited before it could inject the restart. |

One of two permitted scheduling-fault attempts was used in the original
campaign. The direct REST correction and rerun then received a separate limit
of four workload executions and two fault attempts.

The rerun used three of its four workload executions:

| Execution | Mode | Result |
| --- | --- | --- |
| 1 | Test control | Infrastructure failure because the transient user service stopped with the SSH session. |
| 2 | Test control | Passed with no action, no alarm, and all semantic checks passing. |
| 3 | Test fault | Reached the target write, but the synchronous restart action did not finish. |

The rerun used one of its two fault attempts. One workload execution and one
fault attempt remain. That is not enough for another matched control and fault
pair. The confirmation reserve was not eligible because the restart did not
complete and no semantic operator failure was observed.

Fixing the restart action would change another public harness behavior and
requires its own test-driven change. The minimum useful change is to raise the
rerun workload cap from four to five. This leaves two future executions for one
fresh matched control and one fault. If that fault reveals a meaningful
operator failure, a later confirmation pair would also be required before
making a defect claim.

## Evidence handling

All campaign stages used fresh result paths. While a stage was active, only its
systemd state and completion marker were inspected. Every completed VM result
tree was copied in full and checked against VM SHA256 values before review. The
fault tree contains 374 files, and all 374 match the VM manifest. The manifest
itself also matches its VM SHA256 value.

The original local evidence root is
`.cache/helm-controller/upgrade-reliability-20261111/`. It contains the source
review, frozen oracles and plans, all three build results, all seven workload
results, raw controller and coordinator logs, accepted API responses, semantic
outcomes, scheduling proof, failed attempts, transfer evidence, and SHA256
manifests.

The correction and rerun evidence root is
`.cache/rest-list-helm-restart-20261111/`. The corrected control tree contains
378 files, and the fault tree contains 2,320 files. Every file in both trees
matches its VM SHA256 value. The failed transient-service launch and the
persistent-service smoke test were also collected in full and checked against
their VM manifests before review.

The protected dirty file `tests/test_trace_errors.py` was not modified or
staged. Its SHA256 remains
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The five existing untracked Kustomize draft files were not modified, moved,
deleted, or staged. No local Docker, image pruning, credential inspection,
generative evaluation, push, pull request, or upstream publication occurred.
