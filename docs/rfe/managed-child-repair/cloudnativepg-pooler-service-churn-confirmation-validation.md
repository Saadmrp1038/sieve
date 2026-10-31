# CloudNativePG full Service churn: bounded client-impact confirmation

Client date 2026-10-30; execution completed 2026-10-31 UTC.

The confirmation reproduced **full stale-owner Deployment/Service churn and a
real connection refusal to the stale-owned Service**. That client-failure pattern
has now occurred in two full-churn runs with continuous probes. Both also had
longer observed interruptions than their matched no-fault controls.

This supports a reproducible transient availability-regression **candidate**.
It does not isolate the whole measured outage extension to churn or establish a
formal operator-defect claim. Correct-child creation was consistently delayed,
while Pod readiness and DNS timing also contributed to the measured intervals.
There was no persistent outage, incorrect successful result or database failure.

## Matched full-churn results

| Full-churn pair | No-fault failure-to-success span | Fault span | Observed extension | Failed Pooler queries: control / fault |
| --- | ---: | ---: | ---: | ---: |
| Previous full-churn pair | 21.65 s | 24.23 s | +2.58 s | 35/198 / 33/204 |
| New confirmation | 18.76 s | 23.66 s | +4.90 s | 31/200 / 39/208 |

The earlier partial-churn pair (only ServiceAccount and Role) is retained in the
sealed comparisons but excluded from the full-churn confirmation count. Its spans
were 18.69 seconds without a fault and 19.94 seconds with the pause.

Spans run from the first failed query's start to the next successful query's
completion. They include DNS, connection and sampling latency. Failed-query counts
are not duration estimates: slow DNS failures reduce how many attempts fit into
an interval. These are fresh-connection, read-only queries; persistent sessions,
writes and transaction continuity were not tested.

In the new pair, all **495 direct-to-PostgreSQL queries succeeded**. Across both
routes, 903 attempts produced 70 Pooler connection/DNS failures, zero incorrect
successful results and zero post-convergence failures. Both workloads completed
all eleven correctness, evidence-completeness and recovery checks. Those checks
permit measured interruption during deliberate replacement; they are not a
zero-interruption criterion.

## What actually repeated

The old Pooler was API-deleted, a distinct replacement UID was accepted, and the
held old-UID reconciliation resumed only after the new C informer receipt.
It created a ServiceAccount, Role, RoleBinding, Deployment and Service owned by
the deleted UID, plus a ReplicaSet, Pod and EndpointSlice descendant. Full API
histories prove creation and subsequent garbage collection of all eight objects.

New query 60 resolved and attempted `10.96.212.68`, belonging to stale Service
`400b88bc-c699-40d4-8334-93ad78509448`. The Service still existed throughout the
query, but `psql` returned exit code 2 and connection refused, with empty stdout.
Its API-observed lifetime was about 0.210 seconds. The correctly owned replacement
Service used `10.96.53.210`. The prior full-churn run independently captured the
same pattern at a different stale Service UID/IP (query 59, about 0.318 seconds of
Service lifetime). These are actual client requests, not an inference based only
on resource status.

There is also repeated resource-progress delay. Measured from the new Pooler's
API creation, the correct Deployment appeared 1.27 seconds later in the prior
fault than its control, and 1.23 seconds later in this confirmation. Correct
Service creation was delayed by 1.09 and 1.26 seconds respectively. This is
consistent with the extra old-owner creation/garbage-collection cycle delaying
replacement progress.

The entire 2.58/4.90-second client-outage difference is **not causally isolated**.
New Pod creation-to-Ready times were about 16.6 seconds in the prior control versus
20.2 seconds in its fault, and 15.9 seconds in the new control versus 19.6–19.7
seconds in its fault. The unchanged native Deployment uses a ten-second TCP
readiness-probe period. Probe/Pod-readiness timing and DNS resolution can change
the client recovery interval. This experiment has only two full-churn probe-equipped
pairs and did not add a counterfactual that prevents stale child creation while
holding the remaining schedule fixed. Therefore the observed longer interruptions
repeat, but the precise causal outage penalty and operator-bug classification
remain unconfirmed. Normal delete/recreate replacement already interrupts clients.

The native fault reconciliation also logged a transient Service NotFound error;
it retried and converged. The stale ReplicaSet emitted failed-Pod-creation events
when its ServiceAccount had already disappeared. Such native errors and all full
histories remain available; they were not converted into a persistent-failure claim.

## Boundaries and independent review

Exactly one new pair ran, with no automatic retries or extra attempts. Both used
the unchanged `actions: null` control or original after-old-read YAML from runtime
commit `8b8ca1d699ee9bb969d9ff2a73bbb79dd6e2d1f6`, the same test image and the
previously reviewed two learning baselines/oracles. No relearning, builds, source
fixes, new images, worker changes, timing changes, trigger changes, masks or oracle
edits occurred. Per-phase limits were 900 seconds; the finite service limit was
1,950 seconds. The client retained its 420-second loop, four-second query bound
and 0.2-second post-response interval. Both traces ended normally before those
bounds, and the VM had no Sieve service or kind container running afterward.

The native subject remains
`9bb80d2292f5845ac6c2d60dc691b222a5ac4655`. Original Go/module/binary/image
provenance was reused and independently verified, with ten operator workers,
a single controller Pod UID and zero controller restarts per case. All 56 saved
objects in each case equal full independent API reconstruction. Accepted parent
UID/spec receipts, CA, correct child ownership, two Ready replacement replicas,
unchanged Cluster identity/spec, all 32 acknowledged rows on three database
instances, one writable primary, complete client streams, actual container/source
identity, graceful client stops and process outcomes were verified.

The generic after-read physical verdict remains false/unsupported. A separate
strict actual-old-UID/Get/API/informer/hold/release/process audit proves the injected
replacement scenario; no generic proof was weakened. The no-fault plan has no
injected effect. Both top-level test CLIs returned 1 because the original raw
oracle alarms remain; workload and injection processes completed with code 0.
A successful systemd result was not treated as experiment success.

The entire terminal tree was copied and every file SHA256-verified against the VM
before inspection: **11,449 files / 410,283,260 bytes**. All **19 raw alarms** remain:

- 4 learn/test hash differences equal the actual built binaries.
- 14 extra resource-history counts equal the actual extra old-owner cycles and
  descendant events, checked against both learning baselines.
- 1 independent probe-Pod CPU-status presence alarm matches the previously reviewed
  variation: one healthy learning run ended with `28m`, the other with no CPU
  status field; this control had no field and the fault had `28m`. Requested
  resources stayed fixed, and all probe containers completed with no restarts.
  Exact allocation-field timing remains unexplained; it is not a Pooler child
  ownership or client-query failure.

There were no harness aborts, Sieve RPC errors or controller panics. No runtime
fix was needed, so the unchanged source's existing validation (412 Python tests
plus ten subtests, Ruff, strict mypy on 38+4 modules, wheel) was reused rather than
rerun. Fresh VM client, API, native, process and physical-action audits passed.

All 91 prior input/report/metadata/manifest hashes remain unchanged. The four prior
client-impact result trees were fully rehashed again: 45,355 files /
1,710,684,983 bytes, identical. The user's dirty `tests/test_trace_errors.py`
remains untouched with SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
Original campaign and other-operator evidence/images were not changed.

The [sealed confirmation results](cloudnativepg-pooler-service-churn-confirmation-results.json)
retain the raw alarms, measured comparisons, exact source/plan/oracle/image
provenance, complete UID/API histories, query records, process outcomes, manifest
and support-artifact hashes. New evidence is in
`.cache/cloudnativepg/pooler-client-confirmation-20261030/` and
`runs/cloudnativepg-client-confirmation-20261030-vm/`.
The [earlier report](cloudnativepg-pooler-client-impact-validation.md) and its sealed
results remain unchanged. This report is local; no push or PR update was performed.
