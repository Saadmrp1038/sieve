# CloudNativePG remaining 77-plan campaign

October 29, 2026 (America/Chicago; evidence timestamps use UTC).

All **77 distinct remaining original plans were attempted and independently
reviewed** on the fixed native CloudNativePG subject. The original attempts
produced **74 verified fault effects, 75 application passes and two unknown
application outcomes caused by harness failures**. Three separate diagnostic
attempts recovered verified effects and complete application passes for both
harness failures. Across all attempts, **76 of the 77 distinct plans have verified
fault effects**; one plan remained untriggered. No new operator bug is
substantiated. These results do not mean that all 77 original attempts passed.

The [sealed aggregate](cloudnativepg-remaining-plans-results.json) contains every
original `remaining_index`/`original_index`/family/YAML hash, all original and
diagnostic outcomes, physical proofs, all **330 original plus 11 diagnostic raw
alarms**, classifications, exact image/oracle/runtime hashes and evidence paths.
Its SHA256 is
`4ab56139c4836d82676427b6b085a612aa5b793699c7048c34049845344bac33`.
The prior [known-bug calibration](cloudnativepg-sieve-validation.md), including its
four B-to-C attempts and 28 alarms, remains separate.

## Coverage and limits

| Original family | Selected | Attempted | Triggered | Verified effects | Application passes | Unknown | Raw alarms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Intermediate-state: one-action restart | 76 | 76 | 75 | 73 | 74 | 2 | 327 |
| Unobserved-state: C-to-D before-read pause | 1 | 1 | 1 | 1 | 1 | 0 | 3 |
| Total original plans | 77 | 77 | 76 | 74 | 75 | 2 | 330 |

The three diagnostics are repeat attempts of original indices 54, 59 and 69:
two triggered and physically verified restarts, three application passes and
11 retained alarms. They add **zero** new unique plans. Original first-attempt
failures remain unchanged in the aggregate.

- **54:** the original unchanged Cluster-update occurrence-165 restart observed
  only 157 matching writes; its diagnostic observed 163. Both applications
  passed without that restart. Neither attempt is effective fault coverage.
- **59:** the original restart deleted the old controller, but phase B was
  explicitly rejected while its admission webhook was unavailable. The workload
  aborted after accepted phase A, before owner deletion; replacement readiness,
  final SQL and semantic completion remain unverified. A separate unchanged-plan
  diagnostic completed a verified restart and all application checks.
- **69:** the original first-Deployment-create restart began while its Service
  did not yet exist. The workload aborted its immediate ownership check before
  any A/B/C/D update or accepted deletion. The controller was terminating, but
  its DELETED event and replacement completion were unproven. A separate
  unchanged-plan diagnostic verified the complete restart, subsequent Service
  creation with the correct owner UID and all application checks.
- **73:** one additional raw alarm records a real final status difference.
  During active-owner child repair, accepted Pooler status RV `1317` changed
  instances from 1 to an omitted zero. The native updater copies the replacement
  Deployment's zero replica count; its `int32` field uses `omitempty`. The
  restart interrupted after this fourth status write. Both observed
  post-restart Pooler Gets already showed a deleting owner, whose pinned native
  guard returns before status updates. This snapshot remains visible in the
  final raw result. Active-owner status freshness is not established by this
  terminating-owner experiment. Cleanup and database checks passed.

Verified restarts require the old controller UID's actual API DELETED event,
a genuinely new controller UID first seen after deletion began, a final Ready
nonterminating replacement and the matching completed action/process. The
C-to-D miss requires real read holds/releases, overwrite receipts from every
observed cache stream, the exact accepted D UID/resourceVersion/spec/state
returned by the held Get, no C Get and a prior accepted baseline Get.

Every complete application pass independently verifies active-owner repair with
a new Ready child, accepted A/B/C/D mutations, unchanged parent UIDs/specs,
the server-declared deletion generation 1 to 2 followed by stable 2, complete
owned-child watches, no recreated children after owner deletion, and 32
acknowledged SQL rows on all three ready database instances with one writable
primary. Each complete case's 48 saved objects equal full API reconstruction,
including the actual root CA. Missing artifacts in the two original harness
failures stay unverified; initial SQL acknowledgments do not substitute for
their missing final per-instance checks.

## Raw alarm review

| Original classification | Alarms |
| --- | ---: |
| Learning/test executable hashes, each matching its actual built binary | 154 |
| Cross-cluster monitoring RVs, with actual matching references and identical data | 75 |
| API-proven leader Lease holder switch/counter 0 to 1 after restart | 73 |
| Consequences of the two preserved incomplete harness attempts | 27 |
| Retained terminating-Pooler zero-instance status snapshot | 1 |
| Total | 330 |

The three diagnostics retain six executable-hash, three monitoring-RV and two
verified Lease-transition alarms. No oracle, learned mask, YAML, occurrence,
raw verdict or raw alarm was edited. Lease classification requires the same
Lease UID and exactly one holder change from the deleted controller's name to
its distinct Ready replacement after deletion started, with both learning
counters zero. Monitoring data retain canonical JSON SHA256
`3a442a07131007175e3f836bbf1e9b8ec20820775c22110b8a40f1a446ea016f`.
Original indices 30 and 50 happened to match the reference monitoring RV.

Initial reviews requiring diagnosis are preserved for batches 00, 05, 06 and
07. Fresh reviews add the exact API/process/source evidence for classification;
the generic auditor continues to reject unknown alarms. No new operator bug is
claimed from untriggered, incomplete or otherwise unproven effects.

## Fixed inputs and Sieve repairs

The native operator remains `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`, Go
1.27.1, client-go 0.37.0 and controller-runtime 0.25.0. All 1,002 original Go
files in vanilla/test builds remain byte-identical, all original module versions
and controllers remain enabled, and default ten-worker concurrency and paired
vanilla instance managers are unchanged. The exact prior fixed vanilla/test,
Kubernetes v1.36.4 test API/node and pinned operand image IDs are reused.
There were no controller/API rebuilds, plan generation or relearning.

Original indices 0–59 use merged Sieve main
`45653882d6213b6f0d03faaba1bf3a3e03ffde83`. Two logical Python workload fixes
use the approved public workload CLI/external Kubernetes boundary:

1. `94f5a0e05917abcd063a9a6e7afc196c53f28741` narrowly retries explicit
   server-rejected Pooler webhook connection refusals within the existing
   readiness timeout. Every failed command and retry remains recorded. Policy
   denials and exhausted budgets still fail. Original cases 60 and 62 each
   retained eight such rejected PATCH requests, then completed effective
   restarts and full application checks on the VM.
2. `40d9999c5d2a2b9f22f6f9d559e3f5e59992f18a` waits for initial child existence
   before the strict owner-UID check, within the existing readiness timeout.
   Missing children still time out and foreign ownership still fails. In its
   diagnostic, the old controller was DELETED at API position 385, its new UID
   appeared at 389 and the correctly owned Service first appeared at 427.

Each fix has a failing public regression recorded before its production change,
followed by a passing regression and negative controls. Neither adds a
reconciliation wait between accepted A/B/C/D updates. Original indices 60–69
and diagnostics 54/59 use `94f5a0e`; the final seven and diagnostic 69 use
`40d9999`. Each fresh snapshot verifies 650 tracked files and six links; only
the workload, its README and public tests differ from merged main. The immutable
runner bytes retain SHA256
`c456d1c86c56a75ad6814e50962a5fec10c11853b80d3599b66ed9aa12577d2e`.
The original 77-plan manifest retains SHA256
`d9895aa6c21c63d27d63f33df0460cc6879a5b18acbb2dc892c360d90b2a1169`.
All four oracle hashes remain exactly those recorded in the calibration report
and sealed aggregate.

Final validation: **398 Python tests plus 10 subtests**, 15 workload tests,
Ruff lint/format excluding the untouched user edit, configured strict Mypy
(38 files), strict workload typing and wheel build passed. Existing native Go
race/vet and informer-version proofs are reused because their inputs did not
change. No generative evaluation was run. No push or PR was created, and
`tests/test_trace_errors.py` remains untouched and unstaged.

## Evidence preservation

The campaign ran 09:07–19:19 UTC October 29 in eight bounded original batches,
with three diagnostic attempts separately labelled. Every terminal result tree
was copied in full and SHA256-verified against the VM before inspection.
Aggregation reverified **44,125 files / 2,783,781,933 bytes** across the eight
original and two diagnostic result trees. Per-tree manifests and review hashes
are embedded in the aggregate. The initial zero-plan systemd import failure
remains separately preserved as five files / 11,720 bytes; its launcher fix
supplied the immutable snapshot's `PYTHONPATH` before import.

Evidence roots are `runs/cloudnativepg-remaining-batch-*-vm` and
`runs/cloudnativepg-remaining-diagnostic-*-vm`; manifests and supporting
regression/control reports are under `.cache/cloudnativepg/remaining/`.
Eight-hour services, 1,500-second phases, 600-second injection/cluster limits
and the 8 GiB floor were retained. No capacity expansion or pruning was needed
during this campaign. Prior CloudNativePG, cert-manager, External Secrets,
RabbitMQ and SecretGenerator evidence/images remain preserved.

Scope remains one kind node with three database instances, observed Cluster and
Pooler frames and the original finite schedules. This does not establish
multi-host failover, other reconciliation internals, stale-state coverage,
active-owner status freshness or a new upstream defect.
