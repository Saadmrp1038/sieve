# Prometheus Operator reliability profile

The profile pins Prometheus Operator v0.94.2 at commit
`0db90444c4e6fa5672be5bf7d2b15ca9dd1c5ace`. It keeps the upstream Go 1.26
toolchain, client-go v0.37.0, command line settings, RBAC, CRDs, and runtime
base image. The profile does not add worker processes. Prometheus Operator uses
one queue worker for each native resource controller.

The deployment downloads the upstream `bundle.yaml` from the pinned commit and
checks its SHA256 value before applying it. The custom deployment replaces only
the upstream operator image. Prometheus v3.14.0 and the v0.94.2 config reloader
use pinned multi-platform image digests. The deployment records the platform
images loaded into the Kind node.

## Ownership handoff experiment

The `owner-handoff-control` and `owner-handoff-rejected` workloads create a
synthetic Prometheus resource named `sieve-handoff`. Both workloads wait for an
available Prometheus Pod and record the UIDs of the Prometheus resource, its
StatefulSet, and its Pod.

The control deletes the Prometheus resource and waits for Kubernetes garbage
collection to remove the old StatefulSet. It then creates the same Prometheus
resource again and requires the new StatefulSet to refer to the new Prometheus
UID.

The rejected case applies a `ValidatingAdmissionPolicy` that rejects deletion
of only `StatefulSet/default/prometheus-sieve-handoff`. A server side dry run
must prove that the policy is active before the workload deletes the first
Prometheus resource. The workload then creates a Prometheus resource with the
same name and spec. It records whether the operator reports the replacement as
reconciled and available while the StatefulSet still refers to the old
Prometheus UID.

The workload removes the policy after taking the handoff sample. It then waits
for garbage collection and reconciliation to produce an available StatefulSet
and Pod with current owner UIDs. The policy does not reject any create or update
request, and it does not match other StatefulSets.

## Source basis

`pkg/prometheus/server/operator.go` calculates the StatefulSet input hash from
the Prometheus labels, annotations, generation, selected configuration, and
the existing StatefulSet spec. The Prometheus UID and desired owner reference
are not part of that hash. When the hash matches, the controller skips the
StatefulSet update. The desired StatefulSet built by
`pkg/prometheus/server/statefulset.go` does contain the current Prometheus UID.

The status path retrieves the StatefulSet by its fixed name. It retrieves Pods
with the StatefulSet selector and checks the owner kind and name. It does not
require either owner UID to match the current objects. The experiment checks
the combined behavior on a real Kubernetes API server. Source inspection alone
does not establish an operator failure.

## Commands

Run builds and workloads through the public Sieve CLI:

```sh
sieve build -c prometheus-operator -m all -r LOCAL_REGISTRY
sieve vanilla -c prometheus-operator -w owner-handoff-control -r LOCAL_REGISTRY --output FRESH_PATH
sieve vanilla -c prometheus-operator -w owner-handoff-rejected -r LOCAL_REGISTRY --output FRESH_PATH
sieve learn -c prometheus-operator -w owner-handoff-rejected -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root FRESH_ORACLES
```

The workload writes every `kubectl` receipt, every independent API sample, and
the final semantic checks next to the completion marker. A completed workload
can have `semantic_status: failed`. That result means the process completed and
the ownership rule failed. It is separate from a harness failure.

## Validated result

The experiment was validated on Prometheus Operator v0.94.2. The normal control
passed. Kubernetes removed the old StatefulSet before the replacement
Prometheus resource was created, and the new StatefulSet referred to the new
Prometheus UID.

The rejected case exposed an ownership handoff defect. When deletion of the old
StatefulSet was rejected, a replacement Prometheus resource with the same name
and spec received `Reconciled=True` and `Available=True`. The StatefulSet and
Pod still belonged to the deleted Prometheus UID. The operator logged that the
StatefulSet input hash matched and skipped all actions. Removing the policy let
garbage collection and reconciliation recover.

Two Learn executions reproduced the result. A Test image run with no fault
matched them. A scheduled controller restart also reproduced the result after
the replacement controller process had started and synced its caches. This
shows that the stale state is held in Kubernetes objects and is not only in the
controller process.

The full validation report is in
`docs/rfe/managed-child-repair/prometheus-owner-handoff-20261106.md`.
