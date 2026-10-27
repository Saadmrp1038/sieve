# External Secrets instrumented fault validation

October 26, 2026 (America/Chicago; VM artifact names use UTC dates).

Sieve generated and attempted **19 plans**. After fixing a harness teardown
race and replaying five unchanged plans, **12 distinct plans have verified
injections and successful repair: all 10 restarts and two read pauses**. Seven
pause schedules have circular resume dependencies and remain incomplete. No
operator bug was substantiated. This is not a 19/19 pass.

## Subject and provenance

External Secrets v2.14.0 is pinned at
`fc0dd82da6422c6ec840d24425bbda68e5259aab`, retaining client-go v0.37.1,
controller-runtime v0.25.2 and all provider registrations. Builds used the
operator's Go 1.27.3 toolchain. The test build's 812 original operator Go files
were byte-identical to upstream. Learning adds reconciliation observations and
import formatting; operator logic is unchanged.

The profile uses the default single worker, a fake namespaced SecretStore,
five-second Periodic refresh and `creationPolicy: Owner`. `delete-child`
requires recreation after a UID-preconditioned deletion; `drift-child` requires
restoration after a UID-tested data patch. Both require unchanged parent
UID/generation/spec, correct ownership/data, Ready status and 15 seconds of
stable repair. No cloud provider credentials are involved.

The full campaign's host source was `06b6e369c706defc65fdda58a376dbf1fdd4e13c`.
The follow-up host source was `bcdf7049f0f57a069e0f89d215bb6e5b493715ac`.
Each run verified 108 runtime source-file hashes. The follow-up changed host
completion handling only and reused the existing images and oracle byte for
byte. Controller image IDs were checked before reuse.

These are Docker image/config IDs, not registry manifest digests:

| Image in `sieve-eso-20261026` | ID |
| --- | --- |
| `external-secrets:learn` | `sha256:ef61fa8983df0c1cee418fc47fa71a7b6db3e260dd6d9fb9d7d6da4f1e3bfcee` |
| `external-secrets:test` | `sha256:3a8febd7ffa304571fee18eb344bb32da011ebccb2b0503e9f4bac921e0a6ebb` |
| `node:v1.36.4-learn` | `sha256:71ffe52dd2fbd3e47f80dbc1a7db2f39710a80fdf337a4df7e456b50f8823cd6` |
| `node:v1.36.4-test` | `sha256:99809e4f9e76d3606a53dfc575a047580e147a7561188d05603ba86ad7eb1399` |

The Kubernetes 1.36.4 nodes use upstream release components with only
kube-apiserver rebuilt for Sieve. The release archive SHA256 is
`3e1a8a8584953cb3f907987d042ba15b641bdb1a1d3631bfe6a4dee16d96c4a5`.
`nodes-summary.json` retains API binary hashes, component image IDs and build
versions. All 28 learned/test API logs across the campaign and follow-up contain
Sieve API events and the expected `v1.36.4-sieve-learn/test` version. All 28
corresponding saved states capture `configmap/default/kube-root-ca.crt` and
match independent reconstruction from their API logs.

## Executions and coverage

Both vanilla controls and all four learning passes passed. Each workload learned
two fresh baselines before oracle construction and plan generation.

| Workload | Restart plans | Read-pause plans | Stale-state plans |
| --- | ---: | ---: | ---: |
| `delete-child` | 5 | 4 | 0 |
| `drift-child` | 5 | 5 | 0 |
| Total generated and attempted | 10 | 9 | 0 |

The full campaign completed 12 workloads and verified 10 injections: eight
restarts and two pauses. Two late restarts began, but were interrupted by
harness cleanup. Seven pauses held a matching read without releasing it.
There were zero harness exceptions and 22 raw alarms.

The follow-up replayed the exact original YAML bytes for:

- `delete-child/intermediate-state-test-plan-5.yaml`: late recreation restart.
- `drift-child/intermediate-state-test-plan-5.yaml`: late repair-write restart.
- `drift-child/unobserved-state-test-plan-3.yaml`: metadata-informer pause control.
- `drift-child/unobserved-state-test-plan-4.yaml`: full-object informer pause control.
- `delete-child/intermediate-state-test-plan-1.yaml`: early restart control.

**All five follow-up injections and workloads completed with zero raw alarms.**
Each restart has an observed old-pod DELETED event, a distinct replacement UID
and a Ready replacement in the final resource snapshot. Both pause controls
have a matching actual read hold and release. Mutation responses, unchanged
parents, ownership, data, Ready status and stable repair were independently
checked. Every follow-up plan hash matches its original; no plans were amended
and no new plans were generated.

There were 24 fault executions in total, including the five repeats. Fifteen
executions completed both injection and repair; these cover 12 distinct plans.
The seven remaining distinct plans are incomplete, not successful tests.

## Incomplete schedules and raw alarms

The seven incomplete plans all pause the read required to produce their own
resume event. The workload does not supply another event that can break that
dependency.

| Workload / unobserved-state plan | Blocked dependency | Raw alarms |
| --- | --- | ---: |
| `delete-child`, 1 | SecretStore initial read; resume needs its status update or deletion | 4 |
| `delete-child`, 2 | ExternalSecret initial read; resume needs its update or deletion | 4 |
| `delete-child`, 3 | Metadata Secret deletion; recreation needs the paused read | 2 |
| `delete-child`, 4 | Full-object Secret deletion; recreation needs the paused read | 2 |
| `drift-child`, 1 | SecretStore initial read; resume needs its status update or deletion | 4 |
| `drift-child`, 2 | ExternalSecret initial read; resume needs its update or deletion | 4 |
| `drift-child`, 5 | Secret drift read; corrective write is also the resume condition | 2 |

Four cases stop before mutation during initial convergence. Three have accepted
mutations and unchanged parents, but repair remains blocked by Sieve. All 22
original alarms are preserved: seven workload timeouts and 15 end-state
consequences (missing objects/fields or retained drift). No unexplained alarms
remain, and no alarm was removed or masked to obtain the successful replays.
Failure to repair while the required controller read remains indefinitely
paused does not establish an operator recovery bug.

The two interrupted restarts were a separate harness issue. Both triggers fired
and owner deletion started; earlier status messages calling these triggers
unreachable were incorrect. The workload finished about 15 seconds into pod
termination, and cleanup stopped the coordinator too early. Commit `bcdf704`
waits for schedule completion before final capture, using the existing finite
`cluster` timeout and early coordinator-exit detection. The two affected replays
needed an additional 18.57 seconds each; the three control replays needed about
0.09 seconds. Fault actions and oracle verdict rules are unchanged.

## Commands, checks and retained evidence

Recorded VM entrypoints, executed as `Pial` under bounded systemd services:

```sh
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/active-operators/instrumentation/run-sieve-retry3.py
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/active-operators/instrumentation/run-sieve-completion.py
```

These runners retain their exact source as `run-sieve.py` in each result
directory. They embed unique output paths; a new execution needs fresh paths.
The individual CLI phases are `sieve vanilla`, `sieve learn --build-oracle`
and `sieve test -p PLAN`, all with `-c external-secrets`, the recorded registry,
output and oracle-root arguments. See each `summary.json` and command journal
for the exact arguments and per-phase deadlines.

Local evidence is retained under:

- `runs/external-secrets-sieve-retry3-vm/`: 7,235 files SHA256-verified against
  the final VM manifest, including all raw alarms, generated plans and baselines.
- `runs/external-secrets-sieve-completion-replay-vm/`: 808 files similarly
  verified, including the five original plan hashes and completion-wait outcomes.
- `runs/external-secrets-sieve-retry3-review.json` and
  `runs/external-secrets-sieve-completion-review.json`: independent artifact
  review, separate from raw verdicts.
- `runs/external-secrets-instrumentation-build-vm/`: build scripts, patches,
  image/source provenance and retained regression evidence. Earlier failed
  attempts remain in their separate result directories.

The completion fix passed a failing-before-fix workload/result-boundary
regression using real SDK/command boundaries, plus delayed-completion,
never-finished and exited-coordinator cases. Final local validation passed:

```sh
uv run --locked pytest -q             # 295 tests and 10 subtests
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv build --wheel
```

All three Go modules previously passed race tests and vet, including native
client-go cache mutations and actual held/released reads. Go did not change in
the host completion fix. Frozen legacy trace, plan and verdict contracts pass.

## Limits

This validates the pinned operator and fake-provider profile, not cloud
providers, other refresh/ownership policies or arbitrary operator versions.
Zero stale-state plans means no API-pause/reconnect coverage in this campaign.
Seven circular schedules are still a plan-generation/coverage limitation;
pruning or fault-generation semantics were not changed to force completion.
Metadata and full-object receipts have separate observers, but caches of the
same representation still share one observer. The profile has one worker per
reconciler. The complete 19-plan campaign ran before the completion fix; five
targeted plans, rather than all 19, were replayed afterwards.
