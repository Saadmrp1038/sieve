# Prometheus Operator can report a replacement Prometheus as healthy while its StatefulSet belongs to the deleted object

## Status

The behavior is established on Prometheus Operator v0.94.2 at upstream commit
`0db90444c4e6fa5672be5bf7d2b15ca9dd1c5ace`.

The campaign used synthetic resources in a disposable Kind cluster. It ran six
workload executions and one scheduled controller restart. All completed result
trees were copied from the VM and every file was checked against a SHA256 value
calculated on the VM.

## Finding

Prometheus Operator can keep using a StatefulSet that belongs to a deleted
Prometheus resource after a new Prometheus resource is created with the same
name and spec.

The replacement Prometheus resource had a new UID. The StatefulSet and its Pod
still referred to the old UIDs. Even so, the replacement Prometheus resource
reported both `Reconciled=True` and `Available=True`.

The state remained wrong while deletion of the old StatefulSet was rejected.
It recovered after that rejection was removed and Kubernetes could delete the
old StatefulSet.

This is an operator reliability defect. A Kubernetes owner reference identifies
an object by UID, not only by kind and name. A reference to the UID of a deleted
object does not make the StatefulSet a child of a new object that happens to
have the same name.

## Experiment

The workload created `Prometheus/default/sieve-handoff` and waited for its
StatefulSet and Pod to become ready. It recorded all three UIDs.

The normal control deleted the Prometheus resource and waited for Kubernetes to
remove the old StatefulSet. It then created the same Prometheus resource again.

The rejected case installed a `ValidatingAdmissionPolicy` that rejected only a
DELETE request for
`StatefulSet/default/prometheus-sieve-handoff`. A server side dry run proved
that the policy rejected the request. The policy did not reject create or
update requests and did not match any other StatefulSet.

The rejected case then deleted the first Prometheus resource and created a new
one with the same name and spec. It sampled the parent status and the owner
references before removing the policy. It then waited for final recovery.

## Verified coverage

| Run | Image and condition | Result |
| --- | --- | --- |
| Normal control | Unmodified v0.94.2 image, normal garbage collection | Passed all five ownership and status checks |
| Rejected deletion | Unmodified v0.94.2 image, old StatefulSet deletion rejected | Reproduced stale ownership and false healthy status |
| Learn run 1 | Instrumented Learn image, same rejected deletion | Reproduced the same result with 25 observed Sync hooks |
| Learn run 2 | Fresh cluster with the same Learn image | Reproduced the same result with 25 observed Sync hooks |
| Test control | Instrumented Test image with no scheduled action | Matched the Learn oracle and reproduced the result |
| Restart test | Instrumented Test image with one scheduled restart | The restart was physically verified and the result remained |

The normal control changed the Prometheus, StatefulSet, and Pod UIDs. The new
StatefulSet referred to the replacement Prometheus UID. This rules out normal
asynchronous convergence as the explanation for the rejected case.

In the unmodified rejected run, the first Prometheus UID was
`db4f2227-fa25-4a31-82fa-f760a921b28f`. The replacement UID was
`e4750ad4-b206-4306-b73a-21e533c90f2c`. At the handoff sample, the StatefulSet
still referred to `db4f2227-fa25-4a31-82fa-f760a921b28f`. Its UID and the Pod
UID had not changed. The replacement resource still reported
`Reconciled=True` and `Available=True`.

The operator log recorded the message `new statefulset generation inputs match
current, skipping any actions` after the replacement resource was created. The
input hash was `4351411403356834157` before and after the handoff.

## Physical restart evidence

The fault plan restarted the controller after its second CREATE of
`Secret/default/prometheus-sieve-handoff-thanos-prometheus-http-client-file`.
This point occurs before the replacement StatefulSet hash check.

The coordinator recorded that action 0 triggered. Kubernetes removed controller
Pod `prometheus-operator-747b8b8dd7-p7t7w` with UID
`42941ddc-0f9d-4604-b5f4-e7a4fb451145`. It then started controller Pod
`prometheus-operator-747b8b8dd7-tvkkl` with UID
`a6b00323-3642-49da-a250-7f9630aaec98`.

The new process started at 07:15:31 UTC, synced its caches, and logged the same
hash match at 07:15:32 UTC. The fault proof marks the action as physically
verified. The stale StatefulSet ownership and the false healthy status remained.
This rules out a stale value held only in controller memory.

## Source cause

The Prometheus controller calculates a StatefulSet input hash before it builds
the desired StatefulSet. The hash includes Prometheus labels, annotations,
generation, selected configuration, and the existing StatefulSet spec. It does
not include the Prometheus UID or desired owner reference.

The desired StatefulSet builder does set an owner reference with the current
Prometheus UID. However, the controller skips all StatefulSet actions when the
input hash matches. A replacement Prometheus resource with the same name and
spec starts again at generation 1. Its calculated hash can therefore match the
old StatefulSet even though its UID is different. The skip prevents the desired
owner reference from being applied.

The status reporter lists Pods by the StatefulSet selector. It accepts a Pod
owner reference when the kind and name match. It does not require the owner UID
to match the current StatefulSet UID. It can therefore count the ready Pod from
the old ownership chain and mark the replacement Prometheus resource as
available.

The relevant v0.94.2 code is:

* `pkg/prometheus/server/operator.go:1069` calculates the input hash.
* `pkg/prometheus/server/operator.go:1096` skips all actions when the hash
  matches.
* `pkg/prometheus/server/operator.go:1473` defines the hash input.
* `pkg/prometheus/server/statefulset.go:97` adds the managing owner.
* `pkg/operator/factory.go:62` builds that reference with the owner UID.
* `pkg/operator/statefulset_reporter.go:121` lists Pods and accepts an owner by
  kind and name.

The Prometheus repair policy does not cover this case. That policy repairs Pods
from a bad StatefulSet revision. Here, the old Pod is ready and the input hash
matches. The problem is the ownership UID.

## Upstream review

A bounded public issue and pull request search found no report of this exact
same name parent replacement and stale owner UID case.

Pull request
[`#8309`](https://github.com/prometheus-operator/prometheus-operator/pull/8309)
concerns stale informer state around an immutable StatefulSet update. Issue
[`#8312`](https://github.com/prometheus-operator/prometheus-operator/issues/8312)
concerns a version upgrade that produced StatefulSet not found and already
exists errors. Neither covers this ownership handoff.

The current upstream main branch still contains the same hash skip and a hash
input that does not include the parent UID. Its status reporter still accepts
Pod ownership by kind and name without checking the UID. This source check was
made on November 6, 2026. Main was not tested at runtime, so this report does not
claim a runtime reproduction on main.

## Harness failures

The following failures did not establish operator behavior:

* The first vanilla stage stopped before the workload because the deployment
  loader changed a valid CRD sequence entry into a YAML mapping. The deployment
  loader was fixed and the fresh normal control passed.
* The first Learn postprocess reached the end of both workloads but rejected a
  Kubernetes List envelope whose `items` value was null. A behavior test was
  added first. The decoder now accepts both null and populated `items` values.
  The sealed Learn result was copied to a fresh path and postprocessed without
  rerunning the workloads.
* Three Learn launch attempts stopped before cluster creation because of remote
  launch ownership and Git trust settings.
* Two repository transfer attempts stopped before any workload ran.

These failures are preserved separately from the operator result. None is
counted as a successful workload execution or as a scheduling fault.

## Evidence

The complete local result trees are:

* `runs/prometheus-vanilla-control-2-20261106-vm`
* `runs/prometheus-vanilla-rejected-20261106-vm`
* `runs/prometheus-learn-rejected-2-20261106-vm`
* `runs/prometheus-learn-rejected-2-postprocess-20261106`
* `runs/prometheus-test-control-rejected-20261106-vm`
* `runs/prometheus-test-restart-before-hash-20261106-vm`

The failed first deployment is preserved at
`runs/prometheus-vanilla-control-20261106-vm`. Build records and qualified image
details are preserved under `runs/prometheus-build-20261106-vm` and
`runs/prometheus-build-resume-20261106-vm`.

The VM hash manifests, frozen plans, frozen oracle hashes, runner inputs, and
failed launch records are preserved under
`.cache/prometheus/operator-reliability-20261105`.

## Limits

The test used a synthetic Prometheus resource with one replica in a disposable
single node Kind cluster. The deletion rejection was deliberate and narrow. It
models any condition that keeps the old StatefulSet present after the original
parent is gone.

The failure does not occur in the normal deletion control. It lasts while the
old StatefulSet cannot be deleted. Once deletion succeeds, Kubernetes and the
operator recover. The experiment establishes the stale ownership and incorrect
status behavior within those limits. It does not measure how often real users
encounter the required deletion delay.
