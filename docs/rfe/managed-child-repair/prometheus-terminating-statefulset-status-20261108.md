# Prometheus Operator reports a replacement as available while its StatefulSet is terminating

## Status

The experiment established a status defect on Prometheus Operator main commit
`854d33a40caad1dd773f73deb55726732803bcb8`, which reports version 0.95.1. The
remote main reference still pointed to that commit after the runs.

The campaign used three workload executions in disposable Kind clusters. The
normal control passed, and two fresh finalizer cases reproduced the same
failure. The campaign used no scheduling faults and no automatic retries.

## Finding

A replacement Prometheus resource can report `Reconciled=True` and
`Available=True` while its expected StatefulSet is still terminating under the
UID of the deleted Prometheus resource. The replacement owned no StatefulSet,
and its status reported zero replicas, zero available replicas, zero updated
replicas, and zero unavailable replicas.

The false healthy status was stable. Each finalizer case produced five samples
over about 5.7 seconds with the same parent UID, StatefulSet UID, deletion
timestamp, owner UID, and status values. The result was not an ordinary sample
taken between two expected updates.

The replacement recovered after the workload removed the synthetic finalizer.
The operator created a new StatefulSet with the replacement Prometheus UID. The
new Pod first produced `Available=False` while it was not ready, then produced
`Available=True` after it became ready.

## Implementation status

The Prometheus Operator profile now includes the `owner-handoff-finalizer`
workload. The workload runs through the public Sieve command line interface and
uses external Kubernetes process fixtures. It records command receipts,
complete API objects, repeated observations, owner UIDs, deletion state, status
counts, and final recovery.

The change was developed with one failing behavior test before the workload
implementation. Eight focused workload tests passed. The full local test run
passed 730 tests and 10 subtests, with one skipped test. Ruff check and format
validation also passed.

The protected preexisting change in `tests/test_trace_errors.py` was not edited
or staged. Its SHA256 value remained
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.

## Experiment

The workload first created `Prometheus/default/sieve-handoff` and waited for
its StatefulSet and Pod to become ready. It recorded the parent, StatefulSet,
and Pod UIDs.

The normal control deleted the first Prometheus resource and waited for
Kubernetes to remove its StatefulSet. It then created the Prometheus resource
again and required the new StatefulSet owner UID to match the replacement
Prometheus UID.

The finalizer case added `sieve.io/hold-statefulset-deletion` to the first
StatefulSet. It deleted the first Prometheus resource and confirmed that
Kubernetes accepted the StatefulSet deletion. The StatefulSet then had a
deletion timestamp but could not disappear until the workload removed the
finalizer.

The finalizer case created a Prometheus resource with the same name and spec.
It sampled the replacement status and physical ownership state before removing
the finalizer. It then removed the finalizer and waited for complete recovery.

## Verified coverage

| Run | Condition | Result |
| --- | --- | --- |
| Matched control | Normal garbage collection with the unmodified main image | Passed all status and ownership checks |
| Finalizer case | The old StatefulSet deletion was accepted and held by a finalizer | Reproduced the false healthy status across five samples |
| Confirmation case | A fresh cluster repeated the same finalizer sequence | Reproduced the same false healthy status across five samples |

The tested controller image was
`sha256:c3cae46300fecea45b63bf8dbdbbaf9205923408b2d6ec3fd141447a5c029a1e`.
The image was built from the pinned upstream main commit and had no Sieve
instrumentation. The control and both finalizer cases used the same image.

The control result tree contained 343 files and 14,294,678 bytes. Its VM
manifest SHA256 was
`f9a4bc43eae92a1c4e46db5a7c6c089a4ac2b55bfb8a67c71fe6e761601e8595`.

The first finalizer result tree contained 383 files and 14,891,786 bytes. Its
VM manifest SHA256 was
`2120cc99efe3693a40346b51b16fb5eaa8f8bf8e2145fd8c0b5b1d03edb1e7ff`.

The confirmation result tree contained 441 files and 15,801,936 bytes. Its VM
manifest SHA256 was
`29618980f20ff20fde6e770fbc9d2840deebf14b78fdb81928a1c2f90fd6f428`.

Every file in each result tree matched its size and SHA256 value from the VM.
The VM had no running Sieve service or container after the campaign.

## Independent physical evidence

In the first finalizer case, the deleted Prometheus UID was
`7e52f754-3e58-4add-af5f-60a7f6f049eb`. The replacement UID was
`b0207381-71f3-417d-a978-d86243874804`. The terminating StatefulSet UID was
`a63f7626-24c9-4311-9ce1-fd2028c4efa5`, and its controller owner remained the
deleted Prometheus UID.

Kubernetes set the StatefulSet deletion timestamp to
`2026-11-08T21:06:59Z`. Five samples from 21:07:08 to 21:07:13 UTC showed the
same terminating StatefulSet and old owner UID. During every sample, the
replacement reported `Reconciled=True`, `Available=True`, and zero replicas.

The confirmation used new objects. The deleted Prometheus UID was
`0908baa4-83c3-43e3-8708-f0fc83c1b41d`, and the replacement UID was
`debbdff8-6082-4ef6-a4ef-db9c7e3fc685`. The terminating StatefulSet UID was
`09ad0742-0e27-4700-861e-c740326e27c7`. Five samples from 21:15:25 to 21:15:31
UTC reproduced the same status and ownership state.

The command receipts show successful finalizer patches, parent deletions,
replacement applies, and finalizer removals. The controller log records
`object deletion in progress` while processing the replacement. After each
finalizer removal, the log records `creating statefulset`, and the new
StatefulSet uses the replacement owner UID.

## Source cause

The reconciliation path in `pkg/prometheus/server/operator.go` checks whether
the expected StatefulSet has a deletion timestamp. If deletion is in progress,
the controller stops processing that StatefulSet. The source comment explains
that the skip avoids a repeated update and delete loop while Kubernetes
finishes foreground deletion. The experiment confirms that the controller used
that branch.

The status path in `pkg/prometheus/operator.go` also skips a StatefulSet whose
deletion is in progress. It does not add a false or unknown shard status before
the skip. The replacement therefore has no shard status entries, and all
replica counts remain zero.

The `combinedStatus` function returns true when its input is empty. The status
reporter therefore writes `Available=True`, even though it found no usable
StatefulSet for the replacement.

The reconciliation skip is reasonable because an update cannot cancel a
Kubernetes deletion. The status result is the defect. A terminating
StatefulSet owned by a deleted Prometheus resource does not provide an
available workload for the replacement Prometheus resource.

## Upstream history

Commit `2038d841001f6094b79339d394b3d59b3d72cd75` and pull request
[`#4580`](https://github.com/prometheus-operator/prometheus-operator/pull/4580)
added the Prometheus status subresource in April 2022. The implementation
initialized the Available condition to true and skipped a StatefulSet with a
deletion timestamp. The first release containing the code was v0.56.0.

Commit `eb3f85435b5352bb374cdc2e6a336eea5ec60bdd` and pull request
[`#8434`](https://github.com/prometheus-operator/prometheus-operator/pull/8434)
refactored shard status aggregation in March 2026. The refactor kept the same
empty input behavior, and its unit tests explicitly expect both nil and empty
status lists to produce true.

A search of current upstream issues and pull requests found no report for this
exact terminating StatefulSet status case. Issue
[`#8960`](https://github.com/prometheus-operator/prometheus-operator/issues/8960)
and pull request
[`#8961`](https://github.com/prometheus-operator/prometheus-operator/pull/8961)
cover a different path. They concern a retained StatefulSet whose deletion was
rejected, so the hash shortcut can leave the old owner UID in place. The
finalizer experiment uses an accepted deletion and reaches the earlier
`object deletion in progress` branch. Pull request #8961 does not change the
status reporter or the deletion branch.

## Severity

The defect is a status correctness problem. It does not show data loss, and the
replacement recovers when deletion finishes. A long terminating period can
still make health checks and automation trust a false `Available=True`
condition while the replacement owns no workload. The impact is therefore
moderate and depends on how long deletion remains blocked.

## Harness failures

Three initial SSH attempts failed before any remote command or workload began.
The VM alias pointed to the wrong identity file. The existing CloudLab key was
selected without reading or copying its contents, and the alias was corrected.
The VM user also lacked access to the Docker socket, so it was added to the
existing Docker group.

One confirmation preflight command used `jq`, which was not installed on the
VM. The preflight stopped before a workload started. The same checks were run
with the VM system Python, and they passed.

All three workload executions completed with return code zero and left no
running containers. The two semantic failures described above are operator
results, not harness failures.
