# Flux helm-controller reliability profile

The profile pins Flux helm-controller v1.7.1 at commit
`da037f220ee3e97eb91a70506b5c5f169e46bb68`. It keeps the upstream Go 1.27
toolchain, Helm v4.3.1, client-go v0.37.1, controller logic, build flags, and
runtime packages. The isolated Deployment watches the default namespace and
runs ten reconciles with the native `--concurrent=10` option.

The deployment installs the HelmRelease CRD from the pinned controller commit.
It also installs the HelmChart, OCIRepository, and ExternalArtifact CRDs from
source-controller v1.10.0, which is the API version selected by the pinned
controller. The deployment script checks the SHA256 value of every downloaded
CRD before applying it.

## Failed upgrade and remediation experiment

The `failed-upgrade-remediation` workload serves two small charts from an
in-cluster HTTP fixture. Revision 1 installs a ConfigMap, Deployment, and
Service that return `revision-1`. Revision 2 changes all three resources to
`revision-2` and adds one `post-upgrade` hook Job that exits with status 1.

The HelmRelease uses the documented `RemediateOnFailure` upgrade strategy. It
sets `retries: 0`, `strategy: rollback`, and `remediateLastFailure: true`.
Under that policy, the failed revision 2 upgrade must be rolled back once to
the previous successful revision. The HelmRelease must remain not ready because
the requested revision 2 was not installed successfully.

The workload checks four records independently. It reads the HelmRelease
status, decodes only the synthetic release's Helm storage Secrets, reads the
live Deployment and ConfigMap, and sends requests through the Kubernetes
Service proxy. A stable final result requires the Helm storage record, status
history, live resources, and Service response to agree on the rolled back
revision 1.

## Scheduling point

Helm stores a pending upgrade before it changes live resources. After the late
hook fails, Helm changes that stored release to failed. helm-controller observes
the storage writes in memory and copies them into `.status.history` before it
patches the HelmRelease status.

The learning traces established the first creation of
`Secret/default/sh.helm.release.v1.sieve-upgrade.v2` as the stable scheduling
point. The Secret has the `pending-upgrade` status when the write completes. A
controller restart at that point leaves the pending Helm release durable before
the upgrade has finished. The replacement process must recover the pending
release and complete the configured remediation without making status, storage,
live resources, and the Service response disagree.

The scheduling point must appear in two fresh learning traces before it can be
used. A generated or hand-written plan is not enough evidence. The test-image
control uses the same workload and image with no scheduling action.

## Defect threshold

Temporary disagreement while the upgrade or rollback is running is expected.
The workload resets its stability period when the controller Pod identity
changes and requires the final state to remain consistent for the configured
settling period.

The workload reports a semantic failure if any durable Helm storage revision
within the five-entry history limit is absent from `.status.history`. It also
reports a failure if the current HelmRelease generation is Ready, if rollback
does not restore revision 1, or if the live Service response disagrees with
the stored release. A semantic failure is an experiment result and is separate
from a workload or harness failure.

## Commands

Run builds and workloads through the public Sieve CLI. Native builds and
workloads belong on the disposable Linux VM.

```sh
sieve build -c helm-controller -m all -r LOCAL_REGISTRY
sieve vanilla -c helm-controller -w failed-upgrade-remediation -r LOCAL_REGISTRY --output FRESH_PATH
sieve learn -c helm-controller -w failed-upgrade-remediation -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root FRESH_ORACLES
sieve test -c helm-controller -p FROZEN_PLAN -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root VERIFIED_ORACLES
```

The workload saves the two chart archives and their hashes. It also saves every
accepted API response, every observation, decoded synthetic Helm release
metadata, controller Pod identities, live resource state, Service responses,
and the final semantic checks beside the completion marker.

## Source history

The release state machine was introduced by upstream pull request 738 so the
controller could recover releases left in Helm's pending states. Pull request
1580 later repaired Ready and Released conditions after some status patch
failures. The selected hard restart boundary is narrower because the process
can stop before any post-action HelmRelease status patch succeeds.

Upstream main commit `b7b9652a823ae6824b236a87b7e67a3e34ddb27a` does not change
the upgrade, rollback, storage observer, release state, or status patch paths
used by this experiment. A failure still needs native reproduction on current
main and an issue and pull request review before it can be called a current
operator defect.
