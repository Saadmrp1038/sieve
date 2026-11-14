# Argo Rollouts reliability profile

The profile pins Argo Rollouts v1.10.2 at commit
`be8a6221c6c9751cd14497f31a2cb3c33690375a`. It keeps the upstream Go 1.26.10
toolchain, client-go v0.34.5, controller logic, RBAC, CRDs, runtime base image,
and native worker settings. The Rollout, Service, Experiment, and Ingress
controllers each use ten workers. The Analysis controller keeps its upstream
default of thirty workers.

The deployment downloads the upstream `install.yaml` from the pinned commit
and checks its SHA256 value before applying it. It removes only the upstream
Deployment, changes its ClusterRoleBinding to use the test ServiceAccount in
the default namespace, and applies the isolated test Deployment. The workload
uses a pinned BusyBox image that the deployment loads into the Kind node.

## Blue green post promotion analysis experiment

The `bluegreen-post-analysis` workload creates one synthetic Rollout with an
active Service and a preview Service. Revision 1 serves `revision-1` over HTTP.
The workload waits until the Rollout status, both Service selectors, the active
EndpointSlice, and an HTTP request through the active Service all agree that
revision 1 is healthy.

The workload then changes the Pod template to revision 2. Argo Rollouts moves
the active Service to revision 2 before it starts the post promotion analysis.
The workload proves that the active Service returns `revision-2`. The analysis
runs a synthetic Job that waits eight seconds and exits with status 1.

Argo Rollouts should abort the update after the analysis fails. The original
revision 1 ReplicaSet remains the stable ReplicaSet, and the active Service
should return to it. The final check requires the status, Service selector,
active EndpointSlice, ReplicaSet labels, and an HTTP request to agree on
revision 1 for five seconds.

## Source basis and scheduling point

The v1.10.2 controller reconciles the active Service before it reconciles the
post promotion AnalysisRun. A failed AnalysisRun sets the Rollout abort status.
The next reconciliation reads that stored abort status and changes the active
Service selector back to the stable ReplicaSet.

The experiment tests a controller restart between those two operations. The
learning runs must first show a repeatable write that stores the failed
analysis or abort status after promotion and before the Service selector
returns to the stable ReplicaSet. The test run may use that write only after it
appears in two fresh learning traces. A run with the instrumented image and no
restart must pass before the restart run begins.

The restart test checks the controller Pod UID and restart count. It also
resets the five second stability period when the controller Pod changes. A
temporary selector or endpoint change while reconciliation is still running is
not an operator failure.

## Result interpretation

A completed workload passes only when all independent checks agree. The
Rollout must remain degraded and aborted, its stable hash must still identify
revision 1, and the active Service must return to revision 1. The active
EndpointSlice must also contain only revision 1 after the stability period.

A semantic failure is an experiment result. A missing CRD, wrong worker count,
timeout, command error, or missing completion marker is a workload or harness
failure. Source inspection and a generated fault plan do not establish an
operator defect.

## Commands

Run builds and workloads through the public Sieve CLI. Native builds and
workloads belong on the disposable Linux VM.

```sh
sieve build -c argo-rollouts -m all -r LOCAL_REGISTRY
sieve vanilla -c argo-rollouts -w bluegreen-post-analysis -r LOCAL_REGISTRY --output FRESH_PATH
sieve learn -c argo-rollouts -w bluegreen-post-analysis -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root FRESH_ORACLES
sieve test -c argo-rollouts -p FROZEN_PLAN -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root VERIFIED_ORACLES
```

The workload saves every accepted API response, each observation, controller
Pod identities, Service and EndpointSlice state, live HTTP responses, and the
final semantic checks beside the completion marker.
