# External Secrets missed-state replacement validation

October 27, 2026 (America/Chicago).

**Eight new missed-state plans were generated, executed and independently
verified on the VM.** Every plan held and released a real cache Get, prevented
the selected reconciler from reading its intermediate state, and returned the
later independently supplied state. All eight workloads converged with zero raw
alarms. No operator bug was found.

These replace the experimental coverage of the seven circular schedules in the
[original campaign](external-secrets-sieve-validation.md); they do not amend or
complete those original YAML files. Their evidence and 22 original alarms are
preserved separately.

## Workloads and results

Each workload first establishes a healthy fake SecretStore, ExternalSecret and
owned Secret. It then independently submits UID-tested updates A through D,
six seconds apart, without waiting for reconciliation between updates. The
updates change real inputs as well as a label identifying each state:

- `miss-store-updates`: change the Store's fake-provider value.
- `miss-parent-updates`: select successive remote versions in the ExternalSecret.
- `miss-child-updates`: change the owned Secret's marker data.

Store and child workloads preserve the ExternalSecret UID, generation and spec.
Parent updates intentionally change its spec; the final declared generation and
spec, synced generation, Ready status and data must converge. All workloads
check ownership and 15 seconds of stable final data. Accepted API responses are
retained in `workload-updates.json`, and outcome observations in
`update-observations.json`.

| Resource / selected reconciler | B missed, C returned | C missed, D returned | Completed workloads | Raw alarms |
| --- | ---: | ---: | ---: | ---: |
| SecretStore / ExternalSecret reconciler | 1 | 1 | 2 | 0 |
| SecretStore / Store reconciler | 1 | 1 | 2 | 0 |
| ExternalSecret / ExternalSecret reconciler | 1 | 1 | 2 | 0 |
| Owned Secret / ExternalSecret reconciler | 1 | 1 | 2 | 0 |
| Total | 4 | 4 | 8 | 0 |

All three vanilla controls and all six fresh learning passes passed. Generation
produced eight unobserved-state plans, 26 restart plans and zero stale-state
plans. **Only the eight unobserved-state plans were replayed in this campaign.**
No generated YAML was amended. The batch took 56 minutes 33 seconds, including a
99-second test-controller build, nine control/learning executions and eight
fault executions, each using a fresh cluster.

## How the miss was verified

Generation is opt-in for these workloads. A candidate needs evidence that the
selected annotated reconciler read the intermediate state in learning, and that
the accepted external update's UID/resourceVersion reached every observed cache
representation. The planner arms a scoped read pause before API delivery of B
(or C). It resumes only after every required cache has received the independently
supplied C (or D), without a timer or a controller-dependent resume write.

The runtime verifier saves `missed-state-proof.json` and gates the injection
verdict. An independent artifact audit also checked the following directly:

1. All four accepted updates appear with their UID/resourceVersion in the API
   log, and contain the declared value or spec change.
2. A matching cache Get was physically held and released exactly once.
3. Each required cache received the intermediate state before its successor,
   and committed that successor while the read was still held. Both full-object
   and metadata-only Secret streams were checked for the child workload.
4. No recorded Get by the selected reconciler returned the skipped state.
5. The actual held Get returned the successor's exact accepted resourceVersion
   and data/spec with the same UID, after release and before the deferred
   after-read hook. This checks the returned object, rather than only a trigger
   or coordinator completion message.
6. The workload converged; parent invariants, ownership, Ready status, final data,
   stability, successful process outcomes and zero raw alarms were checked.

For example, the first Store case in `test/0000` has these one-based positions
in its `sieve-server.log`: B received at line 78, Get held at 83, C received at
88, release at 98, returned C/resourceVersion `825` at 99, and after-read hook at
100. No B value was returned to the selected ExternalSecret reconciler.
The first child case (`test/0006`) received B in both cache streams, held the
Get at line 83, received C at lines 88/89, released at 103, and returned the exact
accepted C/resourceVersion `840` at 104. The after-read hook is at 105.

The informers receiving B is expected: the fault makes the selected reconciler
miss a cache state, not the watch transport lose an event. Other reconcilers may
still read B. Both streams of a representation share the existing observer
identity; this does not establish arbitrary per-informer identity.

The CA ConfigMap appears in all six learned and eight test states, with matching
API-log evidence. There were zero harness exceptions, zero raw alarms and zero
unexplained alarms. Missing evidence was not treated as success, and no alarms
were suppressed.

## Source and image provenance

External Secrets v2.14.0 remains pinned at
`fc0dd82da6422c6ec840d24425bbda68e5259aab`, with native client-go v0.37.1,
controller-runtime v0.25.2, all providers, the default single worker, five-second
Periodic refresh and `creationPolicy: Owner`. The test build verified all 812
original operator Go files byte-identical to the pin. Operator logic is unchanged.

The runtime manifest verifies 143 tracked files from
`ada30000aa4bef53e72ab5ef4f82be81af08e433`. Logical implementation commits:

- `7f57f93`: match independently produced update states in triggers.
- `81332cc`: record actual returned cache values, including NotFound.
- `85846b4`: independent-update workloads and generation.
- `ada3000`: require returned-read proof for the new workloads.

Only the test-controller image was rebuilt. Learning and Kubernetes images were
reused; the previous test image is retained as `external-secrets:test-bcdf704`.
The IDs below are Docker image/config IDs, not registry manifest digests, in
registry `sieve-eso-20261026`:

| Image | ID |
| --- | --- |
| `external-secrets:learn` | `sha256:ef61fa8983df0c1cee418fc47fa71a7b6db3e260dd6d9fb9d7d6da4f1e3bfcee` |
| `external-secrets:test` | `sha256:273988021a525b4a76df59e2c19425b62c1f0955493b2abd1497166077ebdb49` |
| `node:v1.36.4-learn` | `sha256:71ffe52dd2fbd3e47f80dbc1a7db2f39710a80fdf337a4df7e456b50f8823cd6` |
| `node:v1.36.4-test` | `sha256:99809e4f9e76d3606a53dfc575a047580e147a7561188d05603ba86ad7eb1399` |

Nodes retain upstream Kubernetes v1.36.4 components with only kube-apiserver
instrumented; their build provenance is retained in `nodes-summary.json`.
No cloud provider credentials were needed.

## Commands and evidence

The bounded systemd service ran as `Pial` on `pial-sieve`:

```sh
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/active-operators/independent-updates/run-vm.py
```

The runner is copied as `run-vm.py` into its results. It embeds a unique output
path, so a new run needs a fresh path. The individual commands use the recorded
registry, output and oracle-root settings:

```sh
sieve build -c external-secrets -m test -r sieve-eso-20261026
sieve vanilla -c external-secrets -w WORKLOAD
sieve learn -c external-secrets -w WORKLOAD --build-oracle
sieve test -c external-secrets -p GENERATED_PLAN
```

Build, phase and service deadlines were 2,700, 1,500 and 10,800 seconds. A fresh
cluster required at least 2 GiB of free disk. The service finished normally and
removed its test clusters.

- VM results: `/users/Pial/sieve-ci-20261026/runs/external-secrets-independent-updates-20261027`.
- Local raw evidence: `runs/external-secrets-independent-updates-vm/`; all **4,204
  files** were copied and SHA256-verified against the final VM directory.
- Independent review: `runs/external-secrets-independent-updates-review.json`.
- Audit command: `uv run --locked python .cache/active-operators/independent-updates/audit-results.py`.
- Copy manifests, status and job metadata: `.cache/active-operators/independent-updates/`.

Before the VM run, public workload, learning-postprocess, coordinator/client-go
and verdict tests followed failing-before-fix regressions. Validation passed
309 Python tests and 10 subtests, Ruff, strict Mypy including both workload
entrypoints, wheel packaging, and race tests/vet for all three Go modules. The
injected client remains standard-library-only. Existing frozen legacy trace,
plan and verdict contracts passed.

## Limits

This demonstrates effective missed-state injection and recovery for these eight
schedules, not absence of operator bugs. It covers one release, a fake provider,
one worker per reconciler and two overwrite windows per consumer. The 26 new
restart plans were not executed; no stale-state plans were generated. The
original seven circular schedules remain an unresolved limitation of the legacy
controller-dependent schedule generation. This opt-in path supplies independent
updates instead of changing those legacy fault semantics or treating blocked
workloads as operator failures.
