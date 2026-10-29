# CloudNativePG known-bug calibration and Sieve evidence improvements

October 28, 2026 (America/Chicago; artifact timestamps use UTC).

The VM reproduced the known Pooler lifecycle bug on the upstream parent commit
and passed the same application checks on its fixed commit. **All four injected
runs physically missed the selected intermediate state:** two fixed runs passed
the application checks, and two buggy runs reproduced the lifecycle failure.
The uninstrumented controls showed the same contrast. This calibrates Sieve
against a known bug; it does not establish a newly discovered or fault-caused bug.

All **28 raw alarms remain visible**. The legacy test CLI returned 1 for all four
attempts, including the two semantic passes. The new proof report returns 0 for
each fixed attempt and 1 for each buggy attempt, retaining every raw alarm.

## Counts and physical proof

The public generator produced 78 schedules: 76 intermediate-state/restart,
2 unobserved-state/read-pause and zero stale-state schedules. One generated
Pooler B-to-C before-read pause was selected and replayed unchanged four times.
The remaining 77 schedules were not executed. Repeats are not new unique plans.

| Execution | Runs | Triggered pauses | Verified missed states | Application checks |
| --- | ---: | ---: | ---: | --- |
| Fixed native control | 1 | — | — | Passed |
| Buggy native control | 1 | — | — | Known lifecycle failure |
| Fixed learning | 2 | — | — | Both passed |
| Fixed fault replay | 2 | 2 | 2 | Both passed |
| Buggy fault replay | 2 | 2 | 2 | Both known lifecycle failures |

No CloudNativePG restart or stale-state injection was replayed. All four fault
runs have one matching real read hold and release, a completed injection process
with return code 0, and receipts showing B overwritten by C in every observed
cache stream (`informer` in this case). The paused Pooler consumer never returned
B; its held Get returned the exact later C object independently accepted by the
API, including matching UID and resourceVersion:

| Subject | Repeat | Returned C resourceVersion | Intermediate B returned |
| --- | ---: | --- | --- |
| Fixed | 1 | `1385` | No |
| Fixed | 2 | `1424` | No |
| Buggy | 1 | `1408` | No |
| Buggy | 2 | `1405` | No |

The independent audit reconstructed API writes and state, checked accepted
A/B/C/D responses against cache receipts and returned Gets, and checked the
continuous child watches. The workload holds a Pooler with its own finalizer,
marks it for deletion, deletes its existing Deployment and Service, and supplies
later updates independently of the paused consumer. Both old child UIDs must
have actual DELETED events. The buggy subject then creates new owned Deployment
and Service UIDs; the fixed subject creates neither during the observation
window. Pooler deletion legitimately advances generation 1 to 2: the exact
server-accepted deletion object declares generation 2, which must remain fixed
afterward. Parent UIDs and specifications remain unchanged.

All eight application runs preserve 32 acknowledged SQL rows across three ready
database instances with exactly one writable primary. Active-owner deletion of
a Deployment first verifies repair with a new Ready child UID. The six learned
and injected states capture the root CA and match reconstruction from their
actual API logs. There are no harness exceptions, operator/API/coordinator
panics or observer RPC errors. Buggy workload processes return 1 solely for the
known lifecycle check; fixed workload processes complete with 0.

## Raw alarms and their limits

The two fixed attempts retain three alarms each: two reported executable hashes
differ between learning and testing, and a monitoring ConfigMap resourceVersion
differs between fresh clusters. Each reported executable hash matches the exact
built manager binary. The monitoring reference matches its actual ConfigMap
resourceVersion in every state; ConfigMap data are identical in all six states
(canonical JSON SHA256
`3a442a07131007175e3f836bbf1e9b8ec20820775c22110b8a40f1a446ea016f`).

The two buggy attempts retain eleven alarms each: the application invariant,
six extra-object alarms associated with recreated Pooler children, two executable
hash differences, the intentional parent-versus-fixed source revision and the
monitoring resourceVersion. Across the campaign the classifications are:

| Classification | Raw alarms |
| --- | ---: |
| Independently witnessed known Pooler lifecycle failure | 14 |
| Instrumentation executable hash differences | 8 |
| Intentional subject revision differences | 2 |
| Cross-cluster monitoring resourceVersion differences | 4 |
| Total | 28 |

The two learning ConfigMap versions happened to be equal, so baseline comparison
did not discover this volatile reference field. No mask, oracle, predicate or
raw verdict was amended to obtain a pass. Alarm classification is separate from
the application and injection proofs. The known failure also occurs without an
injection; this experiment does not attribute its existence to the missed state.

## Exact subjects and build provenance

The source pair is the parent and merge commit of upstream
[CloudNativePG PR 11359](https://github.com/cloudnative-pg/cloudnative-pg/pull/11359):

- Buggy: `e819964e03fb638c0acaf13b45a9e862c9e3eadb`.
- Fixed: `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`.

The native Go 1.27.1, client-go v0.37.0 and controller-runtime v0.25.0 graph is
retained: all 235 original module versions are unchanged. Sieve's injected
client is standard-library-only; local SDK copies retain native versions. All
1,002 original operator Go files are byte-identical in vanilla and test builds;
learning changes only observations/imports in two files. Original Dockerfile,
base pin and licenses are retained. Default ten controller workers and all
controllers remain enabled. Cluster and Pooler reconciliation frames are
observed; paired vanilla instance-manager binaries are used in every mode.

Controller builds use Sieve commit
`1a3a11787d5f7e762b3275aabad020c8932bda08`. Runtime snapshot
`2b716f6bc232c42c459c29a4387830f2e1101d2f` verifies 649 source files, with archive
SHA256 `89683c48c0657c1d698e790c7428fcae99f9b18ae3ba662c5da52ff376387466`.
Subsequent host/workload fixes do not change controller build inputs. The native
controls used runtime commit `2e2ea6a789ee9becc6ce2f950b674bb9b19be385`.

Docker image IDs in registry `sieve-cnpg-calibration-20261028`:

| Image suffix | ID (`sha256:`) |
| --- | --- |
| `-fixed/cloudnative-pg:vanilla` | `e8726c439fcef417c6dc7b5bde2eb95d7a96f291519d1fc48f0702ce12be9d3e` |
| `-fixed/cloudnative-pg:learn` | `40f122553e563f2a068a57e822501fdc1e44511061e76e626fb84421e4f90e2e` |
| `-fixed/cloudnative-pg:test` | `cfc186e0238e82269bb27ab3a8312a0cf9b49801d3a774a03111f52da75a4564` |
| `-buggy/cloudnative-pg:vanilla` | `520fec43bdf42c353c8a915133ae7383f430f95ab187b471bf125cdde26d7e70` |
| `-buggy/cloudnative-pg:test` | `c19851441a6689af423d5d5c56fe2c4d7b3782d4f7a4842ebfd67fdd7379c501` |
| `/node:v1.36.4-learn` | `efe50af72929e2602f7e6da5210dadb4f000000e2e9db96a4921fb77cd2b6949` |
| `/node:v1.36.4-test` | `e2edfd6407de5ed4d9d63a1f9458674ce89dd561e48d94ccf5cb4938f0c76f0e` |

Kubernetes v1.36.4 APIs use native Go 1.26.10 and report
`v1.36.4-sieve-learn/test`. API binary SHA256 values are
`fb7d91107d6809ef5dab548c3971625b52f9434d4910422d14cc30187ccc8eef` (learn) and
`b09018cc0fddc33b23a803eece9874ada9d216dc18270fb4213bbfc1c80b1cc1` (test).
The separate host instrumenter uses Go 1.27.3. The release deployment YAML has
SHA256 `37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb`.
Its v1.30.1 origin/banner does not make these pinned source commits equivalent
to an entire published release.

Operand pins are:

- `ghcr.io/cloudnative-pg/postgresql:18.6-system-trixie@sha256:348997438269fddc0e2825e00594367ad61751adb08a7698a0a14f793b0dc2ce`.
- `ghcr.io/cloudnative-pg/pgbouncer:1.25.1@sha256:e6ddfe22d845e603825e235dd8334b21ecd125abea2a2172478f556b8dee2bb8`.

Retained node CRI name and ID lookups bind each immutable repository digest to
its config ID, respectively
`sha256:cad3ed8339b7ca42eb707b1d300cb6668ec6a6aa982684d1495439bd2f27cb6e` and
`sha256:a1b691fd55b36c59f387a0ef65a6e3bd40cd32e199a6cf41b8ed2cca5e84ce8c`.
Docker's index ID and CRI's config ID are different identities; the audit checks
the digest-to-config mapping rather than comparing unlike IDs.

The selected YAML SHA256 is
`469d43f5cd69db5cc4525db2200ed72c91086671311c55d9d9e50503d52b1dd7`.
The original selected bytes and all four effective action lists match. Oracle
hashes remain unchanged throughout:

| Oracle file | SHA256 |
| --- | --- |
| `state.json` | `9d76cf41385b8aa95255a8b8df692fcbca19e622708ed21636a58a385fb60ba2` |
| `controller_family.json` | `eb8bddc69d2185c10fa0b283fa824ec8f4a5aae774afbf724c940102047c997b` |
| `mask.json` | `605aa9a20cd7fc5f4d6bed612cfbf7c4156738343de9d8846c438c639158c10a` |
| `event.json` | `4dd0237506c5628f97b1d2a97cb9a8acbbecd0d239b17a57fe051cf910cca8cd` |

## Sieve changes, TDD and two-axis review

Logical commits add physical fault proofs and explicit application outcomes,
invocation/receiver-aware concurrent traces, baseline occurrence/circular-resume
diagnostics, separate generated/attempted/triggered/effective reporting and the
native calibration workload. Public regressions preceded integration fixes,
including typed process evidence, scoped replacement identities, interval
checkpoint retention, failed-workload diagnostics, continuous lifecycle watches,
accepted-deletion acknowledgement and persistence before blocking waits. The
last persistence regression kills the public workload at that wait boundary.

**Standards review:** two documented P2 findings, both resolved. Strict process
artifact validation is fixed in `41e0edb`; immediate failed-workload diagnostics
are fixed in `56674b0`. One optional DataClumps heuristic concerning invocation
lifecycle maps remains nonblocking; it is not a documented standards violation.

**Spec review:** five findings, all resolved: a preexisting/wrong-namespace
replacement (P1, `41e0edb`), lost sequential interval bounds (P2, `9bc0819`),
polling that missed transient children (P1, `371c1eb`), stalled-watch false health
(P1, `3ba7c19`) and late accepted-delete persistence (P2, `2b716f6`). Findings and
priorities remain separate from the Standards review.

Validation at runtime commit `2b716f6`: 381 Python tests plus 10 subtests; Ruff
lint; formatting excluding the untouched user edit `tests/test_trace_errors.py`;
configured strict Mypy (38 files), strict workload typing and wheel build pass.
All three Go race/vet suites and native informer tests for client-go v0.36.2,
v0.37.0 and v0.37.1 pass on the VM. Earlier failing regressions and race logs are
preserved alongside final logs. No generative evaluation was used.

## Capacity, setup failures and evidence

Capacity was resolved first: root partition `sda3` grew from 64 to 256 GiB without
changing its start or other partitions. All 68,681 existing evidence files/links
and Docker identities remained unchanged. The proof records 197,449,203,712 free
bytes after expansion. No evidence or image pruning occurred. Runs retained an
8 GiB cluster floor, eight-hour service cap, 1,500-second phases and 600-second
injection deadline; services ran as `Pial`.

Four unsuccessful native-control setup attempts executed zero fault plans and
remain whole-directory/hash verified. They exposed a versionless PostgreSQL
digest rejected by native admission, a dangling CRI config ID after kind import,
kubectl tab expansion in a text watch and a legitimate deletion-generation
advance. Public TDD fixes retain `tag@digest`, verify node CRI resolution, parse
structured JSON watches and freeze the accepted deletion object. An initial API
build selected the wrong compiler for the separate instrumenter; its failed
evidence remains alongside the successful native-API build. None required
operator reconciliation changes.

The completed calibration ran 01:38–02:12 UTC October 29 (20:38–21:12 CDT
October 28). Its **14,377 files / 567,142,608 bytes** were copied in full and
SHA256-verified against the terminal VM tree before summary inspection. Native
controls separately retain **11,214 files / 381,223,332 bytes**. Build, module,
API and Go validation trees are also fully hash-verified. Evidence locations:

- `runs/cloudnativepg-calibration-vm` and `runs/cloudnativepg-calibration-review.json`.
- `runs/cloudnativepg-controls-retry4-vm` and `runs/cloudnativepg-controls-review.json`.
- `runs/cloudnativepg-controls{,-retry1,-retry2,-retry3}-vm` for setup failures.
- `runs/cloudnativepg-build-vm`, `runs/cloudnativepg-module-proof-vm`,
  `runs/cloudnativepg-api-build{,-retry1}-vm` and
  `runs/sieve-improvements-verification-vm` for provenance and checks.
- `.cache/cloudnativepg/*-result-hashes.json`,
  `.cache/cloudnativepg/capacity/proof.json` and
  `.cache/cloudnativepg/validation/proof-report-*.log` for manifests and offline reports.

Recorded entrypoints (use fresh output/manifest paths for any future execution):

```sh
# On the VM: immutable runner, immutable runtime snapshot, fresh result path.
python .cache/cloudnativepg/run-calibration.py SNAPSHOT RESULT
# Locally: whole-tree collection before independent inspection.
uv run --locked python .cache/cloudnativepg/collect-final.py \
  .cache/cloudnativepg/job.json .cache/cloudnativepg/calibration-result-hashes.json
uv run --locked python .cache/cloudnativepg/audit-calibration.py \
  runs/cloudnativepg-calibration-vm runs/cloudnativepg-calibration-review.json
uv run --locked python .cache/cloudnativepg/finalize-review.py
uv run --locked sieve report --proofs \
  --results runs/cloudnativepg-calibration-vm \
  --plans runs/cloudnativepg-calibration-vm/selected
```

The last command counts the selected corpus as one plan, with four attempts and
four verified effects; it exits 1 because the two buggy application checks fail.
Each fixed case reported separately exits 0 and still displays its three alarms.

Scope remains limited: one Cluster and Pooler on one kind node, with three
database instances. This is not multi-host failover validation. Cluster and
Pooler frames observed maximum overlap one in this workload; 32-worker overlap
is tested by public Go/RPC fixtures, not demonstrated by this application run.
Other reconciliation and instance-manager internals, the upstream DBRole fix,
unselected schedules and stale-state faults remain outside this calibration.
Informer observations identify the representation, not each individual informer
instance. Complex restart/stale-state sequences remain explicitly unverified by
the initial physical-proof implementation. The optional invocation-map cleanup
and raw-oracle volatility described above remain visible follow-up opportunities.

## Whole-branch PR review, October 29

The final review compares the whole branch with fork main
`2c9206ae0b6408e30575c5c306fbeb7b3c9c7b59`. Standards review found no documented
breach; its schema-version strictness advisory is fixed in `c6e22c6`. The optional
invocation-map Data Clumps heuristic remains nonblocking. Spec review found two
P2 defects, both resolved: `dcef5fd` preserves opaque legacy reconciliation IDs
that happen to begin with `SIEVE-V2:`, and `2bc983d` prevents saved attempts
without proofs from disappearing from report totals or raw alarm counts.

Each fix has a failing public-boundary regression recorded before its production
change. Re-reporting the preserved calibration still yields four attempts, four
verified misses, two semantic passes, two known lifecycle failures and all 28
raw alarms. Operator/runtime inputs and original evidence remain unchanged;
these reporting and decoder fixes do not require new subject images or fault
replays. Standards and Spec re-reviews found no outstanding actionable findings.
