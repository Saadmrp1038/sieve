# Maintained-operator recovery validation

Checked October 26, 2026, on SSH host `pial-sieve`. All eight recovery cases
passed. No operator bug was observed in these cases. These are uninstrumented
published-image checks, not Sieve fault-plan runs.

## Targets

Both projects had recent substantive maintainer activity and published releases
on October 23, 2026:

| Operator | Tested release | Deployment |
| --- | --- | --- |
| External Secrets | [v2.14.0](https://github.com/external-secrets/external-secrets/releases/tag/helm-chart-2.14.0) | Official chart, fake provider, Periodic refresh every five seconds, `creationPolicy: Owner`. |
| cert-manager | [v1.21.3](https://github.com/cert-manager/cert-manager/releases/tag/v1.21.3) | Official release manifest, SelfSigned Issuer, ECDSA Certificate, `rotationPolicy: Always`. |

Maintenance checks included cert-manager's October 24 CloudDNS fix
[#9490](https://github.com/cert-manager/cert-manager/pull/9490) and External
Secrets' October 23 release work
[#7178](https://github.com/external-secrets/external-secrets/pull/7178).

Each operator ran on its own disposable Kubernetes v1.36.4 kind cluster. This
is within cert-manager v1.21's documented Kubernetes
[support range](https://cert-manager.io/docs/releases/). The node image was
`kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed`.
The isolated tools were kind v0.33.0, kubectl v1.36.4 and Helm v4.3.1.

cert-manager used its supported `--enable-certificate-owner-ref=true` flag so
the common repair runner could check ownership. This is a non-default
garbage-collection policy and limits the result to that configuration. No
operator source or binary was changed, and no cloud credentials were needed.

Downloaded release assets were checked against GitHub's SHA256 digests:

| Asset | SHA256 |
| --- | --- |
| `external-secrets-2.14.0.tgz` | `0a6d9e91b1ff50adfdbf24b50b582babfa7c5b7df42b21974ca4b3fb88ee5e34` |
| `cert-manager.yaml` | `c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74` |

Observed controller image IDs were
`ghcr.io/external-secrets/external-secrets@sha256:10608eb53e636e744903d5f3657a4d9c44e28d9d9c181b73e07bc7dd2e6884dc`
and
`quay.io/jetstack/cert-manager-controller@sha256:9f4f96a33c7774cef24466fcd85031997531a03a8d117f151126baa8d1290dcc`.

## Results

Each row is one execution per operator, not repeated statistical evidence:

| Case | External Secrets | cert-manager |
| --- | --- | --- |
| Delete an owned Secret with its controller running | Recreated; passed | Recreated; passed |
| Corrupt an owned Secret with its controller running | Expected marker restored; passed | Valid TLS material restored; passed |
| Delete while controller pods are absent, then restart controller | Repaired; new Ready pod; passed | Repaired; new Ready pod; passed |
| Corrupt while controller pods are absent, then restart controller | Repaired; new Ready pod; passed | Repaired; new Ready pod; passed |

Review checked accepted mutation responses, correct ownership, unchanged parent
UID/generation/spec, a new Secret UID after deletion, and 15 seconds of stable
repair in every case. The outage cases independently observed the missing or
corrupted Secret while the main controller was scaled to zero, then required
distinct replacement pod UIDs and Ready conditions in the saved API snapshots.
Admission webhooks and certificate helpers stayed running.

External Secrets restored `marker: expected-value` and reported Ready.
cert-manager reported Ready for the current parent generation. OpenSSL checks
of the retained cert-manager results independently verified certificate
validity, hostname, server usage and matching certificate/private-key public
keys. Both test clusters were removed after evidence collection.

## Reproduction and evidence

The [workload instructions](../../../experiments/active-operators/README.md)
describe setup, commands and assertions. The VM batches invoked:

```sh
uv run --locked python experiments/active-operators/workload.py \
  external-secrets delete-child runs/case/workload.log
uv run --locked python experiments/active-operators/workload.py \
  cert-manager drift-child runs/case/workload.log
```

Each command also supports the other mutation. The batch runner gave each
workload 600 seconds overall; convergence/repair waits were bounded at 180
seconds each and settling at 15 seconds. Local command tests include failed
repair, unobserved mutations, stale Ready status and mismatched TLS keys.

Validation commands passed:

```sh
uv run --locked pytest -q                     # 287 tests, 10 subtests
uv run --locked ruff check .
uv run --locked ruff check experiments/active-operators/workload.py
uv run --locked mypy                          # 32 source files
MYPYPATH=src uv run --locked mypy experiments/active-operators/workload.py
```

Raw results are retained locally in `runs/active-external-secrets-vm/` and
`runs/active-cert-manager-vm/`. Their corresponding VM paths are
`/users/Pial/sieve-ci-20261026/runs/active-external-secrets-20261026/` and
`/users/Pial/sieve-ci-20261026/runs/active-cert-manager-20261026/`.

Each directory contains the final summary, exact executed workload and batch
scripts, API command outputs, mutation and repair evidence, controller logs and
cleanup result. cert-manager also retains its installed manifest. Local
`reviewed.json` files record the independent checks. All 393 External Secrets
files and 712 cert-manager files were compared with VM-generated SHA256
manifests after transfer. Raw evidence stays outside Git.

## Coverage limits

- No Sieve coordinator or instrumented controller/API server ran. Generated
  Sieve plans, executed Sieve plans and effective Sieve injections are all zero.
  Raw Sieve oracle alarms were not produced; this is not a zero-alarm Sieve run.
- The controller outage is a graceful scale-down followed by a mutation and
  startup recovery. It does not test an in-flight crash, held reads or stale
  observations.
- External Secrets' fake provider and five-second periodic refresh do not
  validate remote providers or prove repair depends on child watch events.
  cert-manager's SelfSigned path does not cover ACME, DNS providers or external
  issuers. Each configuration manages one parent/Secret pair.
- Passing these checks provides working workloads and correctness criteria for
  instrumentation work. It does not yet establish increased Sieve bug-finding
  capability. Current controller-runtime/client-go instrumentation must be
  validated before learning traces and injecting faults into these releases.

The earlier independently reproduced Secret Generator issue and unresolved
historical RabbitMQ manual replay retain their original evidence and verdicts.
Neither is reclassified by these passing current-operator checks.
