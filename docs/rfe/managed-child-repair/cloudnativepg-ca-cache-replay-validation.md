# CloudNativePG CA Secret race: bounded Sieve replay

The documented shared-CA race reproduced in **2/2 affected runs** and **0/1 fixed run**. Both affected runs failed the transient bootstrap-error invariant. All six workloads eventually reached three ready instances, inserted 32 acknowledged rows and read the expected rows from all three instances with exactly one writable primary. This validates a known-bug reproduction; it is not a new discovery or evidence of a permanently stuck cluster.

Upstream: [issue 10988](https://github.com/cloudnative-pg/cloudnative-pg/issues/10988), [fix 10989](https://github.com/cloudnative-pg/cloudnative-pg/pull/10989). Affected revision `105265f5b702b7dcfaa41f7d8e1a45123f1c8ee1`; fixed revision `96cb6b05aea5f76ddac43d21e0f178e0a8b1b331`. Their source diff contains only `cluster_pki.go` and its upstream regression test. The fix reuses the already returned server CA when client and server CA names match.

## Comparison and physical evidence

Two fixed learning baselines and the affected no-fault control passed all six application checks with no auxiliary-object error in the full API timeline or native logs. The subsequent finite fault stage ran affected/fixed/affected on 2026-10-31, 22:17:57–22:33:02 UTC.

| Fault run | Revision | Shared-CA Create requests | Actual API-cache hold | Application result |
| --- | --- | --- | --- | --- |
| buggy-fault-1 | 105265f | 3 (1 after release) | 149.315 ms | target error; recovered |
| fixed-fault | 96cb6b0 | 1 (0 after release) | 62.708 ms | passed; no error |
| buggy-fault-2 | 105265f | 2 (0 after release) | 196.782 ms | target error; recovered |

The identical YAML pauses API Secret-cache receipt of the first shared CA event. It resumes on `(duplicate CA Create AND bootstrap-error phase) OR replication-certificate Create`. No timeout releases, worker reductions, watch changes or forced cache contents were used. Each run has one real hold and release and one persisted CA UID with unchanged data and correct owner. Both affected runs issued the target duplicate Create **during the hold** and recorded the actual client-CA `AlreadyExists` error in both native logs and accepted API Cluster status. The fixed run issued one CA Create, reached the replication-certificate release predicate and recorded no bootstrap errors. Cache `NotFound` is inferred from the unchanged native code path; it is not claimed as a directly recorded cache read.

The first affected run additionally retried server-CA creation after release, producing another real `AlreadyExists` error. Its one-second workload sampler saw that later error; the full API timeline preserves the preceding target client-CA error. This extra retry remains separate. It does not establish that the client-CA fix removes every server-CA race. Repeated API snapshots carrying the same error are not counted as separate failures.

Every one of 43 saved objects per case matches independent full-API reconstruction. Actual reported operator binary/commit values match the native builds. There were ten workers and zero controller restarts. Both server and replication certificates verify against the actual shared CA. SQL receipts and the final 30-second readiness observation pass in all cases. SQL checks precede that observation and occur after readiness; this run does not measure continuous client availability during bootstrap.

## Provenance, alarms and preserved failures

Native Go `1.26.4`, client-go `0.36.2` and controller-runtime `0.24.1` were retained; 115 original declared module versions and original Go sources were independently checked. Existing Kubernetes `1.36.4` API images and pinned PostgreSQL operands were reused. Every arm uses the same fixed vanilla instance-manager image. Exact image IDs, binary hashes, source bundles, snapshot manifests and oracle hashes are in the sealed results.

Fault YAML SHA256: `3783132722e923cbaf0a011ea8034e7f923742e1076d62e179ac1d3e0619a40f`. No-fault YAML: `095e63de0cc35e6cb832074423a461c25f077991af333e9b13843f45031fb228`. Plans are frozen from `a1433c4`; workload source is `9d29fb2`. The later Python reporting repair does not change the workload, native builds, plan, learned reference or comparison policy.

All **8 original fault alarms plus 3 separately reprocessed control alarms** are retained and classified: eight built-binary differences and three pinned-commit differences. The transient failure is detected by the workload invariant and independent API/native audit; the final-state/history alarms contain only these provenance differences. No oracle masks or raw verdicts were changed. Generic physical verdicts remain `false` because that auditor does not support this API action sequence. A separate independent physical audit verifies the actual effects and rejects all **18 mutated-evidence negative controls**.

Preserved setup/reporting failures include the initial source checkout, the installation manifest missing `DatabaseRole` (zero workload attempts), the no-fault postprocessor exception, and the initial supplemental auditor's assumption of exactly two CA requests across the entire run. Fresh source-matched installation snapshots corrected setup. The supplemental auditor still requires exactly one target duplicate during the hold and its real client-CA error; each additional post-release request needs its own corresponding server-CA API error. Its failed initial review remains unchanged.

The empty-action postprocessor repair is local commit `19dbc11`: public CLI regression RED before the one-line guard and GREEN afterward; a different event count still raises its history alarm. **423 Python tests + 10 subtests**, Ruff, format, strict mypy on 38 sources and wheel build passed. Reprocessing used a separate artifact copy, with zero additional workloads. The original no-fault exception remains preserved.

Exactly six workload attempts were used: two learning, one affected no-fault, two affected fault and one fixed fault. Workload phases are bounded at 900 seconds, the two-pass learning CLI at 1800 seconds and each stage at 3000 seconds, with zero automatic experiment repeats.

Five complete new result trees contain **10,357 files / 427,282,604 bytes**, copied and SHA256-verified before inspection. Final verification also rechecked all **280,220 files / 11,740,278,591 bytes in 41 prior/current evidence trees**, plus all 64 protected input hashes. The user's dirty `tests/test_trace_errors.py` remains unchanged. The VM is idle; images and all failed attempts remain preserved. No push or PR was made for this work.

Sealed [results](cloudnativepg-ca-cache-replay-results.json) SHA256: `e3f85c9c8c047cf950efb94c82f528613ec59f1389f596ee8f68662c93fd28bc`. Local supporting evidence: `.cache/cloudnativepg/ca-cache-replay-20261031/`; complete result-tree paths, manifests, every alarm and supplemental/failed reviews are indexed in the results file.
