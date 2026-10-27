# Current-operator recovery checks

The original study tested published releases without Sieve instrumentation.
External Secrets now has a dedicated [Sieve profile](../../controllers/external-secrets/README.md),
which reuses its repair workload. The cert-manager experiment remains an
uninstrumented recovery check. The original study's results do not establish
fault-injection coverage.

The October 26, 2026 study targets External Secrets v2.14.0 and cert-manager
v1.21.3 on Kubernetes v1.36.4. Both operators published releases on October 23
and had recent maintainer activity. Sources and reviewed VM results are in
[the validation report](../../docs/rfe/managed-child-repair/maintained-operators.md).

## Setup

Use a disposable cluster and an explicit `KUBECONFIG`. The workloads create
fixed names in the default namespace. The study used kind v0.33.0 with:

```text
kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed
```

Install the corresponding operator before running a workload:

- **External Secrets:** install the official
  [v2.14.0 chart archive](https://github.com/external-secrets/external-secrets/releases/download/helm-chart-2.14.0/external-secrets-2.14.0.tgz)
  with `helm install external-secrets external-secrets-2.14.0.tgz --namespace
  external-secrets --create-namespace --wait --timeout 5m`. The workload uses
  the official fake provider, `refreshPolicy: Periodic`, a five-second refresh
  interval and `creationPolicy: Owner`. It needs no cloud credentials.
- **cert-manager:** apply the official
  [v1.21.3 manifest](https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml).
  Add `--enable-certificate-owner-ref=true` to the `cert-manager` controller's
  arguments and wait for all three deployments to become Ready. This supported,
  non-default garbage-collection setting supplies the owner reference required
  by the shared repair checks. The workload creates a SelfSigned Issuer and an
  ECDSA Certificate. OpenSSL must be available to verify the resulting Secret.

No operator source changes are involved. The downloaded asset hashes and exact
deployed manifests are retained with the study evidence.

## Workloads

```sh
uv run --locked python controllers/external-secrets/workloads/workload.py \
  delete-child runs/external-delete/workload.log
uv run --locked python experiments/active-operators/workload.py \
  cert-manager drift-child runs/certificate-drift/workload.log
```

Both targets accept `delete-child` and `drift-child`. Deletion uses a UID
precondition; drift uses a UID-tested JSON patch. Drift replaces the External
Secret's `marker` or the certificate Secret's `tls.crt` with an invalid value.

The command requires initial convergence, an accepted mutation, an unchanged
parent UID/generation/spec, correct ownership and stable repair. Deletion must
produce a new child UID. External Secrets must restore the expected marker;
cert-manager must restore a currently valid certificate for the requested host
with a matching private key. A stale Ready condition cannot establish repair.

`--repair-timeout` defaults to 180 seconds per convergence wait and
`--settle-seconds` to 15. Process and request deadlines also apply. Evidence is
saved beside `workload.log` even on failure; only success writes
`FINISH-SIEVE-TEST`.

## Controller-outage cases

After a passing baseline, scale only the main controller deployment to zero
and wait for its pods to disappear. Start the workload, independently observe
the deletion or invalid value through the API, then scale the controller to
one. Require repair and a Ready controller pod with a different UID. Leave the
admission webhook and certificate helper running.

This is a graceful controller outage with a mutation during downtime. It does
not inject an in-flight crash or exercise Sieve's trace-guided schedules. The
study's exact orchestration scripts and command outputs are retained in its
result directories.
