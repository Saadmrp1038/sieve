# Mittwald Secret Generator

This integration tests Secret Generator v3.5.0 at commit
`f7e44605aa7980c305f0e600de1613e3c0e57efd` with Kubernetes v1.24.10.
Sieve instruments its client-go dependencies and the reconcile entry points
listed in `config.json`. The operator's reconciliation logic is unchanged.

## Workload

`create-update-recreate` creates a `StringSecret`, updates its marker, deletes
it, waits for the owned Secret to be garbage-collected, and recreates the
`StringSecret` under the same name. It checks the generated 16-character hex
password, marker, Secret ownership, and status reference. The recreated custom
resource must have a new UID, and its final state must remain consistent after
a further 15 seconds.

The driver waits for both reconcile workers before creating the first object.
During a missed-state injection it defers intermediate convergence checks so
that the workload can deliver the update or deletion needed to release the
pause. Final convergence and ownership checks are always required.

## Managed-child repair workloads

`delete-child` deletes the generated Secret while retaining its StringSecret.
`drift-child` changes the Secret's `data.marker` and waits for its intended value
to return. Both require the original parent UID, generation and spec, correct
child ownership, and a matching status reference. Deletion also requires a new
child UID. They run through the same workload command as the lifecycle case.

These workloads write `repair-observations.json`, including the mutation response
and observed resources, even on failed repair. The normal `FINISH-SIEVE-TEST`
marker is written only on success. Each wait defaults to 120 seconds; direct
workload invocations accept `--repair-timeout SECONDS`.
Successful repair is checked for another 15 seconds before finishing;
`--settle-seconds SECONDS` controls that interval for direct invocations.

The pinned controller watches StringSecret changes rather than owned Secrets.
Its documented contract describes parent-triggered creation and updates.
Missing unchanged-parent repair is a credible reconciliation-bug candidate;
README ambiguity alone does not establish that this behavior is intentional.
Run these workloads without faults first; do not learn an oracle from a failed
baseline or change the parent to force repair. See the
[upstream investigation](../../docs/rfe/managed-child-repair/secret-generator-upstream.md)
for existing reports, version identities and an independent released-image test.

## Build and run

Use an amd64 Linux host with Docker, Go, kind, kubectl, and the prerequisites
from the [porting guide](../../docs/port.md). The validated Python dependencies
and Docker 29 setup are documented in the
[experiment notes](../../experiments/secret-generator/README.md).
Run these commands from the repository root. The image namespace is a local
tag prefix; these commands do not push images.

```sh
uv run --locked sieve build -m learn --kubernetes-version v1.24.10 -r sieve-local
uv run --locked sieve build -m test --kubernetes-version v1.24.10 -r sieve-local
uv run --locked sieve build -c kubernetes-secret-generator -m learn -r sieve-local
uv run --locked sieve build -c kubernetes-secret-generator -m test -r sieve-local

uv run --locked sieve learn -c kubernetes-secret-generator \
  -w create-update-recreate --build-oracle -r sieve-local
```

`--build-oracle` runs the workload twice and learns a baseline for this
environment. Generated oracles are intentionally ignored by Git. Plans are
written under
`runs/learn/<run-id>/kubernetes-secret-generator/create-update-recreate/learn/`.
Run a plan with:

```sh
uv run --locked sieve test -c kubernetes-secret-generator \
  -p path/to/plan.yaml -r sieve-local
```

## Reconciler scope for the Secret deletion pause

Two reconcilers read Secrets. Pausing both after a Secret deletion can block
the StringSecret reconciler from recreating the Secret, preventing the plan's
resume trigger from ever occurring. For this schedule, set `pauseBy` on the
`pauseController` action to:

```yaml
pauseBy: github.com/mittwald/kubernetes-secret-generator/pkg/controller/secret.(*ReconcileSecret).Reconcile
```

This holds the Secret reconciler's read while the StringSecret reconciler can
recreate the Secret and produce the resume event. `pauseBy` is optional; omitting
it keeps the pause applicable to all annotated reconcilers. Generated plans do
not infer this dependency automatically, so review the Secret deletion plan
before running a batch.

A completed action sequence alone is insufficient evidence of an injection.
For a read pause, inspect `sieve-server.log` for both `Pause controller at` and
`End pause controller at` for the requested point and scope, and require the
workload to complete with no unexplained alarms.

## Deployment sources

The CRDs, RBAC, and license are copied from the pinned operator release. The
deployment supplies Sieve's image placeholders, pod label, API server address,
and configuration ConfigMap. See [UPSTREAM-LICENSE](UPSTREAM-LICENSE).
