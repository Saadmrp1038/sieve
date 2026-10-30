# CloudNativePG Pooler replacement: continuous client impact

Completed 2026-10-30 UTC. A real client attempted a Service recreated with a
deleted Pooler owner UID and received connection refused. This establishes that
stale-owner churn can reach the client path. It does **not** establish a
reproducible additional outage or a new operator defect: deliberate replacement
also interrupts clients without Sieve, and only one probe-equipped run reproduced
the full Deployment/Service churn.

The two fault runs recovered about 1.25 and 2.58 seconds later than their matched
controls. That difference is a candidate observation, not an established causal
increase beyond normal replacement variability. All routes recovered; there were
no incorrect successful results, direct-database failures or post-convergence
failed queries in these bounded runs.

## Matched observations

| Pair | Schedule | Failed Pooler queries / attempts | First failure to next success | Failed direct queries / attempts | Extra resources created with deleted owner |
| --- | --- | ---: | ---: | ---: | --- |
| pair1 | No fault | 31/203 | 18.69 s | 0/241 | none |
| pair1 | Stale-read pause | 32/200 | 19.94 s | 0/242 | ServiceAccount, Role |
| pair2 | No fault | 35/198 | 21.65 s | 0/245 | none |
| pair2 | Stale-read pause | 33/204 | 24.23 s | 0/262 | all five direct children + descendants |

Each interval starts when the first failed fresh-connection query starts and ends
when the next successful query completes. It includes DNS/connection/probe latency
and is not an exact SLA outage measurement. Failed-query counts alone are not a
fair duration comparison because DNS failures take longer than fast refusals.

The vanilla setup control had 27/195 Pooler failures over an 18.81-second recovery
span, with 0/238 direct failures. Two healthy learning runs had 33/201 and 30/200
Pooler failures over 21.20 and 19.48 seconds; their direct routes had 0/247 and
0/242 failures. Across all seven workloads: 3,118 query attempts, 221 Pooler
connection/DNS failures, 1,717 successful direct queries, zero bad results and zero
post-convergence failed queries. All eleven final correctness, evidence-completeness
and recovery checks passed per workload. These checks permit measured interruption
during deliberate replacement; they are not a zero-downtime criterion.

## Focused client evidence

In pair 2, the old Pooler UID was API-deleted before the replacement UID was
created. The held old reconciliation then created five directly owned resources
(ServiceAccount, Role, RoleBinding, Deployment and Service), plus a ReplicaSet,
Pod and EndpointSlice descendant. All eight were garbage-collected.

The stale Service `c082fc94-cf99-4ba7-b4b3-58c937e1e8ce` advertised
`10.96.203.193` for about 0.318 seconds. Client query 59 started while that Service
existed and finished after its deletion. Its actual `psql` exit code was 2,
stdout was empty, and stderr identifies that exact IP with connection refused.
The correctly owned replacement Service used a different IP. This proves the
observed destination and failure, rather than inferring client impact solely
from Kubernetes Ready conditions. It does not prove that this query would have
succeeded in a no-fault counterfactual at the same instant.

Pair 1 reproduced only ServiceAccount/Role churn. The held reconciliation logged
RoleBinding creation followed by a NotFound reconciler error and returned before
Deployment/Service creation. The following reconciliation converged with the
correct UID. Exact internal cause of that NotFound response was not established;
garbage-collection timing is an inference. The full data-plane churn and its
client consequence were therefore observed once with the continuous probes.

## Method and bounds

The separate `pooler-replacement-clients` workload creates an independent Pod
with two query containers: one uses `sieve-pooler.default.svc`, the other
`sieve-db-rw.default.svc`. A public synthetic read-only benchmark login replaces
any need to obtain existing client credentials. Each query opens a new connection
and checks `32|528|32` (row count, ID sum, matching payload count). Every attempt
retains wall/monotonic timestamps, stdout, stderr, timeout and exit code.

Both routes must warm up successfully before phase B. The existing UID-guarded
Pooler deletion/recreation sequence, ten-second replacement delay and thirty-second
post-convergence observation remain in place. Queries run every 0.2 seconds after
the previous response, with a four-second hard query bound and a 420-second loop
bound plus at most one in-flight query. Complete contiguous records, explicit
terminal records, clean container exits and successful recovery queries are required.
Persistent sessions, writes and transaction continuity were not tested.

The run cap was honored: one vanilla setup control, two learning baselines and
two matched test-image no-fault/paused-read pairs; no automatic repeats. Phases
were limited to 900 seconds, the setup service to 1,050 seconds and subsequent
services to 1,950 seconds. No Sieve service or kind container remained running.

Both pauses independently proved an actual nonterminating old-UID Get held across
old API deletion, new UID creation and new C informer receipt, followed by an
actual release and completed injection process. Generic after-read physical
verdicts remain unsupported/false; the strict supplemental proof is separate.
The no-fault plan has `actions: null`, with no actual hold/release. Original pause
actions are unchanged apart from the separate workload name. No trigger, worker,
watch, oracle or mask was relaxed.

The native subject remains upstream
`9bb80d2292f5845ac6c2d60dc691b222a5ac4655`, with original Go/module/binary
provenance, the same pinned controller/API/operand images and ten operator workers.
Only the Python workload/probe, test profile, plans, boundary tests and documentation
changed; there were no controller/API/compiler/image builds or generative runs.
Runtime source is local commit `8b8ca1d699ee9bb969d9ff2a73bbb79dd6e2d1f6`.

## Independent review and retained alarms

All four terminal trees were copied in full and every file was SHA256-checked
against the VM **before** result inspection: 45,355 files, 1,710,684,983 bytes.
All 56 saved objects in each of the six instrumented cases equal independent full
API reconstruction. Accepted UID/spec responses, CA, correct ownership, two Ready
replacement replicas, unchanged Cluster identity/spec, all 32 acknowledged rows on
three database instances, one writable primary, process completion, zero controller
restarts and probe-container image/source identities were checked independently.

All 28 raw alarms remain in the sealed result:

- 8 learn/test binary-hash differences equal the actual built binaries.
- 18 extra creation/deletion counts match the deleted-owner resources and
  descendants in full API histories, compared with both learning baselines.
- 2 missing CPU status-field alarms concern the independent probe Pod. Its terminal
  `status.resources.requests.cpu` was `28m` in one healthy learning run and absent
  in the other, and absent in both second-pair control and fault. Requested resources
  stayed identical; both probe containers completed normally with zero restarts.
  Exact kubelet allocation-field timing is unestablished. This is not evidence of
  a Pooler operator failure; the raw alarms and API history are preserved.

Initial reviews remain intact. The first classifier assumed the previous full
five-child churn and correctly stopped on the partial first pair; its replacement
reports the actual narrower scope. The second-pair reviewer stopped on the unknown
probe CPU-field alarm until the actual learning/control/API evidence above was
reviewed. No raw verdict or oracle was edited to advance. The client auditor's
positive case and five negative controls passed; the old-read auditor's actual
positive and wrong-UID/missing-C/missing-release negative controls passed.

Local validation: RED/GREEN public executable/workload tests; 412 Python tests
plus 10 subtests; 12 focused tests; Ruff lint/format; configured strict mypy on
38 source files plus four workload modules; wheel build. The user's dirty
`tests/test_trace_errors.py` remains byte-identical (SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`).
All 85 preserved prior metadata/report/manifest inputs remain identical. The five
previous replacement result trees were independently rehashed again: 55,639 files,
2,086,813,466 bytes, unchanged. Original 77-plan outcomes, prior diagnostics, other
operator evidence and images were not changed or merged into these new counts.

The [sealed results](cloudnativepg-pooler-client-impact-results.json) retain every
raw alarm, per-case metrics/UID histories, exact plan/oracle/source/image provenance,
four whole-tree manifests and support-artifact hashes. Evidence and review helpers
are in `.cache/cloudnativepg/pooler-client-impact-20261029/`; full trees are in
`runs/cloudnativepg-client-impact-{control,learn,pair1,pair2}-vm/`. These reports and
workload changes are local; no push or PR update was performed.
