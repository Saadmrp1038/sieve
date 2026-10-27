# External Secrets generated restart validation

October 27, 2026 (America/Chicago).

**All 26 retained restart plans ran with verified injections and successful
workload convergence.** There were zero raw alarms, zero harness exceptions
and no substantiated operator bugs. Together with the
[eight verified missed-state plans](external-secrets-independent-updates-validation.md),
all 34 distinct plans generated for the independent-update workloads have now
been executed successfully. This does not include the separate original
19-plan repair campaign or claim that its seven cyclic schedules completed.

## Results and physical effects

| Workload | Selected original plans | Attempted | Verified restarts | Converged workloads | Raw alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| `miss-store-updates` | 7 | 7 | 7 | 7 | 0 |
| `miss-parent-updates` | 12 | 12 | 12 | 12 | 0 |
| `miss-child-updates` | 7 | 7 | 7 | 7 | 0 |
| Total | 26 | 26 | 26 | 26 | 0 |

These are the original `intermediate-state` family YAMLs from the previous
learning run, each containing one `restartController` action. They target
observed writes such as Store status, ExternalSecret finalizers/status, Secret
creation, value changes and drift repair. Every copied and executed YAML hash
matches its original. No plans were regenerated or amended; no diagnostic
replays were needed.

An independent artifact audit, separate from the runner's success flags,
verified each execution:

- Exactly one restart action completed. The API log shows the old controller pod
  before its DELETED event, followed by a different replacement UID. The final
  live Pod/Deployment snapshot contains a Ready, nonterminating replacement.
- The coordinator and its separate injection process completed. The workload
  process also completed successfully. A CLI zero exit alone was insufficient.
- All four external A/B/C/D updates were accepted with one input UID and distinct
  resourceVersions, and each accepted version occurs in the API log with the
  declared data/spec change.
- Store and child workloads preserved parent UID, generation and spec. Parent
  updates preserved UID and converged to the final declared generation/spec and
  synced generation. Ownership, Ready status, final Secret data and 15 seconds
  of stable convergence were checked.
- Every saved state exactly matches reconstruction from its API log, including
  the CA ConfigMap in all 26 cases. Application state agrees with final workload
  reads; the live snapshot covers the controller Pods/Deployments.
- All original raw verdicts and alarms were retained. None were suppressed;
  every raw alarm list is empty.

The batch took **1 hour 38 minutes 26 seconds**. The existing bounded
post-workload coordinator wait was exercised, taking up to 18.57 seconds in a
case where the workload finished while restart completion was still pending.
The service finished normally and left no running test containers.

## Unchanged subject and reused inputs

External Secrets v2.14.0 remains pinned at
`fc0dd82da6422c6ec840d24425bbda68e5259aab`, retaining native client-go v0.37.1,
controller-runtime v0.25.2, all provider registrations and the default single
worker per reconciler. The profile uses a fake SecretStore, five-second Periodic
refresh and `creationPolicy: Owner`. No provider credentials were needed.

There were no production-code changes, builds or new learning runs for this
batch. The runtime remained `ada30000aa4bef53e72ab5ef4f82be81af08e433`, with 143
source-file hashes verified before replay. Only documentation changed after
that revision. The prior test build verified all 812 original operator Go files
byte-identical to the pinned source; its provenance is retained unchanged.
Operator logic and fault behavior are unchanged.

Images were checked against the preceding validated campaign before reuse.
These are Docker image/config IDs, not registry manifest digests, in registry
`sieve-eso-20261026`:

| Image | ID |
| --- | --- |
| `external-secrets:test` | `sha256:273988021a525b4a76df59e2c19425b62c1f0955493b2abd1497166077ebdb49` |
| `node:v1.36.4-test` | `sha256:99809e4f9e76d3606a53dfc575a047580e147a7561188d05603ba86ad7eb1399` |

The node retains upstream Kubernetes v1.36.4 components with the Sieve API
server. All 26 API logs identify `v1.36.4-sieve-test`. The exact prior oracle
files were copied unchanged, and the independent audit compared them byte for
byte with the validated baseline directory.

## Commands and retained evidence

A bounded systemd service ran as `Pial` on SSH host `pial-sieve`:

```sh
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/active-operators/restart-plans/run-vm.py
```

Each case used this CLI shape, with its exact arguments recorded in the
summary and command journal:

```sh
sieve test -c external-secrets -p ORIGINAL_PLAN_COPY \
  -r sieve-eso-20261026 --output CASE_DIRECTORY --oracle-root COPIED_ORACLES
```

The runner embeds a unique result path; a new run needs a fresh path. Per-case
CLI and overall service limits were 1,500 and 14,400 seconds; the existing
cluster/coordinator wait remained 600 seconds. The runner retained ordinary
verdict failures rather than hiding them or stopping the remaining cases. It
required 2 GiB free before each fresh cluster; no disk cleanup was needed.

- VM results: `/users/Pial/sieve-ci-20261026/runs/external-secrets-independent-restarts-20261027`.
- Local raw evidence: `runs/external-secrets-independent-restarts-vm/`; all
  **11,194 files** were copied and SHA256-verified against the final VM directory.
  One transient SSH copy failure was retried successfully before verification.
- Independent review: `runs/external-secrets-independent-restarts-review.json`,
  with old/replacement pod UIDs and checks for every plan.
- Audit: `uv run --locked python .cache/active-operators/restart-plans/audit-results.py`.
- Job, original plan selection and copy manifests:
  `.cache/active-operators/restart-plans/`.

The unchanged implementation had already passed 309 Python tests plus 10
subtests, Ruff, strict Mypy/workload typing, wheel packaging and race/vet checks
for all three Go modules. This follow-up validates the existing code against
all 26 additional generated schedules rather than claiming a new test-suite run.

## Limits

Passing these 34 schedules establishes effective injection and successful
recovery for the exercised inputs; it does not establish absence of operator
bugs. Coverage remains one release, the fake provider, one refresh/ownership
policy and default concurrency. Zero stale-state plans were generated. The
original seven controller-dependent cyclic pause schedules and their 22 raw
alarms remain preserved separately; this result does not complete them.
