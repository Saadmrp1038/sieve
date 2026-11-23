# OpenTelemetry Operator v0.162.0

This profile pins release `v0.162.0` at commit
`989089bf5c75d78fdc6004147f97d972fc436bd4`. It retains client-go v0.37.1,
controller-runtime v0.25.2, Go 1.26.3, the upstream manager entry point, and the
operator's native reconciliation concurrency. The operator does not expose a
worker-count setting, so the profile does not add one.

The `operator.clusterobservability` feature gate is enabled. The official
release resources and the separately shipped alpha ClusterObservability CRD are
downloaded from the pinned release and commit and checked with SHA256. The
official cert-manager v1.21.3 dependency supplies the conversion and admission
webhook certificate. Only the manager Deployment is replaced with the Sieve
image and moved to `default`; native webhooks, RBAC, resource builders, and
controller logic remain enabled.

## Ownership handoff workload

`cluster-ownership-handoff` creates one ClusterObservability object and waits
for its agent Collector, cluster Collector, Instrumentation object, DaemonSet,
and StatefulSet to become ready with a complete ownership chain. It then deletes
and recreates the parent under the same name.

The workload also creates the standalone TargetAllocator required by the
upstream ClusterObservability end-to-end fixture. Its image is pinned to the
operator release and loaded into the isolated node before the workload starts.

The normal control waits for Kubernetes to delete every old child before it
creates the replacement parent. The fault form sets
`SIEVE_OTEL_BLOCK_COLLECTOR_DELETE=1`. It installs a synthetic
ValidatingAdmissionPolicy that denies deletion of only the two generated
OpenTelemetryCollector objects. Instrumentation deletion remains allowed. The
workload independently records the accepted parent deletion, a denied dry-run
child deletion, old and new UIDs, owner references, native status, underlying
workload readiness, and controller Pod identity.

Creating an admission policy does not prove that API-server enforcement is
active. Before deleting the parent, the workload polls a server-side dry run
until it observes the policy's exact denial message. After changing the binding
to Audit, it polls the same dry run until deletion is allowed before checking
recovery.

A replacement parent may report Ready only if all three direct children have
its UID as their controller owner. A stable terminal disagreement is a semantic
failure, not a harness timeout. The policy binding is changed to Audit after the
measurement so garbage collection can retry. Recovery requires new or adopted
children owned by the replacement parent.

The initial bounded campaign permits five workload executions and one
deletion-denial fault, with zero automatic retries: one vanilla qualification,
two fresh learning runs, one matched test-image control, and one fault run.
Every launched workload counts, including setup failures. If the fault stops
before the parent is deleted because admission enforcement has not yet become
active, a preserved budget amendment may authorize one deliberate replacement
run after the propagation check is fixed. This is not an automatic retry.

```sh
uv run --locked sieve build -c opentelemetry-operator -m all -r sieve-lab
uv run --locked sieve vanilla -c opentelemetry-operator -w cluster-ownership-handoff -r sieve-lab
uv run --locked sieve learn -c opentelemetry-operator -w cluster-ownership-handoff --build-oracle -r sieve-lab
uv run --locked sieve test -c opentelemetry-operator -p PLAN -r sieve-lab
SIEVE_OTEL_BLOCK_COLLECTOR_DELETE=1 uv run --locked sieve test -c opentelemetry-operator -p PLAN -r sieve-lab
```

Only successful controls may produce learned references. Instrumentation must
be qualified from trace observations before fault results are interpreted. A
completed coordinator or a configured admission policy does not establish the
fault. The retained deletion probe must show that the Kubernetes API server
actually rejected deletion of the named Collector child.
