# Pooler readiness-probe isolation

Completed 2026-10-31. This bounded experiment **did not establish a reproducible
additional client outage specifically caused by full Service churn**. With an
actual one-second TCP readiness probe, the full-churn fault had a 0.63-second
longer observed interruption than its matched control. A separate fault that
recreated only RBAC resources differed by 0.72 seconds.

The earlier 2.6–4.9-second matched penalty did not persist at that magnitude.
The reduction is consistent with coarse readiness timing being a major confound.
It does not prove that churn has no effect: full churn still delayed creation of
the correctly owned Deployment and Service, and only one fast-probe fault
reproduced full Service churn. The subsecond client difference is within the
remaining one-second probe cadence and DNS/sampling uncertainty and was not
unique to Service churn. No operator bug is substantiated by these results.

## Observations

| One-second-probe pair | Actual churn | No-fault span | Fault span | Difference |
| --- | --- | ---: | ---: | ---: |
| 1 | ServiceAccount and Role only | 10.42 s | 11.14 s | +0.72 s |
| 2 | Full Deployment/Service churn | 10.56 s | 11.19 s | +0.63 s |

These are monotonic first-failed-query-start to next-successful-query-completion
spans, not precise SLA outage measurements. Wall-clock measurements independently
agree within microseconds. The native-default ten-second-probe full-churn pairs
previously measured 21.65/24.23 seconds (+2.58) and 18.76/23.66 seconds (+4.90).
Those earlier artifacts and results remain unchanged.

Two new healthy learning baselines measured 9.49 and 10.74 seconds of Pooler
interruption. Across all six new workloads, 2,401 fresh-connection queries included
1,251 successful direct-to-PostgreSQL queries, 1,150 Pooler queries and 131 Pooler
connection/DNS failures during deliberate replacement. No successful query returned
bad data, no direct query failed, and no query failed after convergence. All six
workloads passed twelve final correctness, completeness and recovery checks;
these checks permit measured interruption during replacement.

Both fault plans physically held an actual nonterminating old-Pooler-UID Get across
old API deletion, new UID creation and new C informer receipt, then actually released
it and completed the injection process. The first old reconciliation stopped after
ServiceAccount/Role churn; it is a coverage limit for the Service-specific question.
The second created five resources owned by the deleted UID (ServiceAccount, Role,
RoleBinding, Deployment, Service), plus ReplicaSet, Pod and EndpointSlice descendants.
Full API histories prove all eight were garbage-collected. Correct replacement
children converged with two Ready replicas and the requested new UID/spec.

No query in the fast full-churn case addressed the brief stale Service IP. Failed
requests addressed DNS absence or the correct replacement Service before readiness.
This does not exclude a shorter unsampled impact. The earlier two actual refused
requests to stale-owned Service IPs remain real observations under the default probe.

## Timing decomposition

In the fast full-churn pair, measured from new parent API creation, the correct
Deployment appeared 0.69 seconds later and the correct Service 0.91 seconds later
than in the control. This confirms a resource-progress cost of the held old work
and extra creation/garbage-collection cycle; it is not proof of a zero-cost race.

Once the correct Deployment existed, first correct Pod readiness took 7.87 seconds
in the control and 7.81 seconds in the fault. Correct-Service creation to a Ready
endpoint took 7.89 versus 7.53 seconds. From that endpoint event to sustained client
recovery took 0.21 versus 0.60 seconds. The RBAC-only fault also delayed correct
Deployment/Service creation by 0.50/0.64 seconds. Thus the complete subsecond client
difference is not uniquely attributable to Service creation/deletion; the comparison
also includes held-reconcile progress, endpoint publication, DNS and probe sampling.
The evidence supports a small residual cost but does not isolate a reproducible
Service-specific additional outage. There was no persistent failure or data loss.

## Controlled intervention and bounds

The separate `pooler-replacement-fast-probe` profile uses the operator's supported
Pod template. Only readiness period changes from ten seconds to one. TCP port5432,
five-second timeout, failure threshold3, success threshold1 and zero initial delay
remain equivalent to the native probe. No always-success health check, readiness
bypass or publish-not-ready routing was introduced. Both old/new Poolers and both
matched arms use the same setting. Actual admission responses, every observed
Deployment and every actual original/stale/replacement Pooler Pod were independently
checked for that effective probe. Initial mismatch fails before B/deletion, and
replacement mismatches fail the final proof.

The original after-read pause/resume action definitions are unchanged. Both matched
arms retain the ten-second replacement delay, UID-guarded Background deletion,
new two-replica identity, independent new-C release, thirty-second stable observation,
unchanged client program, synthetic benchmark login, 0.2-second post-response query
cadence, four-second query bound and420-second loop bound plus one in-flight query.
Persistent sessions, writes and transaction continuity were not tested.

The declared cap was honored: two fresh learning baselines and two matched pairs,
six workloads total, no automatic retries or extra trials. Each public CLI phase
was bounded at900 seconds; finite services at1,950 seconds. A failing matched
no-fault application's correctness/recovery checks gate its following fault. The
VM had no running Sieve service or kind container at completion.

The native upstream subject remains `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`,
with the same pinned operator/API/PostgreSQL/PgBouncer images, native Go/module/binary
provenance and ten operator workers. No native operator/API input or image was
changed or rebuilt. New-profile oracle files were learned before faults and remained
byte-identical throughout their replays; earlier profiles, YAMLs, oracles and evidence
were not amended. No plan generation or generative evaluation was run.

## Harness and evidence review

Initial public-boundary tests exposed fake executable cold starts exceeding their
accelerated scenario deadlines. Fixture initialization was moved outside scenario
measurement via `kubectl version --client`. Non-timeout client fixtures now have a
two-second command budget; the deliberate-timeout test retains0.2 seconds and real
VM queries retain four seconds. Deadline-negative controls continue to fail closed.
Initial failure logs and the available complete failed boundary trees are retained.

A host wall-clock advance then made a continuous client stream look discontinuous.
Public CLI regressions first failed for both directions: a wall advance falsely
rejected a continuous monotonic stream, and a real15-second elapsed gap hidden by
wall time was accepted. Sampling-gap validation now uses the already recorded
monotonic timestamps with the same ten-second maximum. Both controls pass after
that correction. Independent causal-clock review rejects wall/monotonic alignment
changes above50 milliseconds; its actual-positive and wall-step-negative controls
passed. All actual VM traces stayed aligned within microseconds, so this repair did
not change their measured outcomes.

Learning and pair1 used sealed runtime `b262f3c`; pair2 used a fresh sealed `dd7f4be`
snapshot. A launch-time AST comparison verifies that the only non-test change is
that exact end-of-run gap expression, evaluated after client stops. API/SQL actions,
client program, bounds, health predicates, fault definitions and native inputs are
unchanged. The runner is byte-identical. Reusing the unaffected new-profile learning
oracles did not change any comparison or suppress a verdict. Runtime archives,
source manifests, runner hashes and all source variants are retained.

The new template also exposed a typed-serialization mismatch in the original
independent proof: at exactly the same UID/resourceVersion, the API omits empty
`spec.template.metadata` and container `resources`, while typed cache/informer
serialization emits `{}`. The original failed reviews remain. The corrected proof
permits only those absent empty-struct additions; populated fields still compare
exactly. Wrong old UID, changed cached probe period, missing C receipt and missing
actual release negative controls all reject. Saved-state/API comparison remains
fully exact and is never normalized. Generic after-read physical verdicts remain
false/unsupported; strict supplemental actual-UID/read/API/informer/process evidence
proves the scenario. No generic proof, oracle, raw verdict or mask was weakened.

All three terminal result trees were copied in full and every file SHA256-verified
against the VM **before inspection**: 34,243 files /1,329,272,999 bytes. All56 saved
objects in each of the six cases equal independent full API reconstruction.
Accepted UID/spec responses, CA, correct ownership, unchanged Cluster identity/spec,
all32 acknowledged rows on three DB instances, exactly one writable primary,
complete query streams, actual probe container/source identities, native binaries,
worker count, one controller UID, zero controller restarts and process outcomes
were checked independently. Raw CLI errors were not mistaken for experiment
application failure or success: test CLIs retain their oracle alarms, while the
application and injection processes complete normally.

All26 raw alarms are retained and substantiated:8 actual learn/test binary-hash
differences and18 actual extra old-owner creation/deletion counts, compared with
both learning baselines. There were no unexplained new alarms at final review.
No native panic, Sieve RPC error or VM workload abort was observed.

Final validation: **418 Python tests plus10 subtests**,35 focused boundary tests,
Ruff lint/format, configured strict mypy38 plus four workload modules, and wheel.
Local logical code commits are `5d100d2`, `b262f3c` and `dd7f4be`. All265 prior input,
report and metadata hashes remain identical; the five previous client-impact and
confirmation trees were rehashed again:56,804 files /2,120,968,243 bytes, unchanged.
The dirty user file `tests/test_trace_errors.py` remains untouched at SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
Original campaign and other-operator evidence/images remain intact.

The [sealed results](cloudnativepg-pooler-probe-isolation-results.json) contain every
raw alarm, phase/case counts, source/plan/oracle/image provenance, physical/readiness
and clock proofs, full UID histories, timing decomposition, failed reviews, diagnostic
artifacts and complete manifest/support hashes. Full new evidence is under
`.cache/cloudnativepg/pooler-probe-isolation-20261030/` and
`runs/cloudnativepg-probe-isolation-{learn,pair1,pair2}-vm/`. This report is local;
no push or PR update was performed.
