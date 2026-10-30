# CloudNativePG plan 54: investigation and bounded rerun

October 29, 2026 (America/Chicago; evidence timestamps use UTC).

The single authorized rerun **triggered naturally at occurrence 165, completed
an API-proven controller restart and passed all application checks**. It
observed 175 matching writes overall. The original YAML, occurrence, ten-worker
configuration, images, oracles and physical/application proof requirements
were unchanged. No production source change or additional replay was needed.

This extends distinct effective coverage to **77/77 original plan identities
across all attempts**. It does not turn the original attempts into 77 passes.
The completed [77-plan aggregate](cloudnativepg-remaining-plans-results.json),
both earlier untriggered plan-54 attempts and all prior failures remain intact.

## Why the earlier attempts did not trigger

Plan 54 is original index 55, `intermediate-state-test-plan-58.yaml`, SHA256
`b21ee834332f07caee2a5f1cb64e385a37d3ff9dfbaabc8452b1c040879c9acc`.
Its sole restart action requires the 165th successful update of
`cluster/default/sieve-db` observed after a write by `ClusterReconciler.Reconcile`.
It has no state-difference predicate. Update, Patch, StatusUpdate and StatusPatch
all count; failed REST requests do not.

| Preserved run | Matching writes | In “Waiting for the instances to become active” | Other phases |
| --- | ---: | ---: | ---: |
| First learning baseline | 192 | 165 | 27 |
| Second learning baseline | 187 | 160 | 27 |
| Original plan-54 attempt | 157 | 130 | 27 |
| First diagnostic | 163 | 136 | 27 |
| This bounded rerun | 175 | 148 | 27 |

Both earlier coordinator logs contain fewer than 165 eligible notifications.
Every observed notification was sent and completed: 157/157 and 163/163.
Neither had a satisfied action, controller deletion or RPC/panic marker. Their
applications passed, but the injection processes timed out at the existing
600-second deadline. Their matching write streams ended after roughly 136 and
142 seconds; waiting longer within the injection deadline added no matches.

The pinned native readiness gate requeues after one second while instances are
not active. The observed count variation lies entirely in that startup phase.
Thus this is a reachability shortfall for a timing-sensitive occurrence count;
the evidence does not establish a lost notification at the coordinator. It also
does not isolate which scheduling, readiness or queue events caused each count.
Learning support alone does not guarantee replay reachability.

The count is not a stable semantic checkpoint: occurrence 165 happened during
the waiting phase in learning, but after the cluster became healthy in this
rerun. The unchanged YAML asks for the ordinal write, so that later trigger is
valid evidence of its specified fault, not proof of an identical startup state.

## Rerun constraints and independent result

One fresh service ran 20:15:02–20:21:28 UTC (15:15–15:21 CDT). It had a
30-minute service cap, the existing 1,500-second phase and 600-second injection
limits, and no automatic repeat. It reused sealed runtime
`40d9999c5d2a2b9f22f6f9d559e3f5e59992f18a`, the unchanged fixed native subject
`9bb80d2292f5845ac6c2d60dc691b222a5ac4655` and the existing immutable runner.
No generation, relearning, image rebuild, capacity change or pruning occurred.

Exactly 165 matching write notifications preceded the satisfied trigger. The
independent audit verifies:

- Old controller UID `516277f4-be89-4aeb-8025-75a28ad1b014` was actually DELETED
  by the API; distinct replacement UID
  `5ce43409-2e71-4a5d-828c-339e074e2134` first appeared after deletion began and
  was Ready and nonterminating. The matching single action and injection
  process completed successfully.
- Accepted A/B/C/D responses have API resourceVersions `1485`, `1507`, `1545`
  and `1572`. Parent UIDs/specs remained fixed; deletion declared generation 2,
  which stayed 2. Active-owner child repair succeeded, complete child watches
  acknowledged deletions, and no owned children were recreated afterward.
- All 48 saved objects equal full API reconstruction, including the root CA.
  All three ready database instances retained the 32 acknowledged rows with
  one writable primary. Native binary/source/image and ten-worker proofs pass.

All four new raw alarms remain visible: two learning/test executable hashes,
one actual monitoring-ConfigMap RV difference and one strictly API-proven
leader-Lease transition from counter 0 to 1. The raw test CLI returns 1 for
these alarms; the public proof report returns 0 for the effective fault and
application pass. No admission retry was needed. No new operator bug is
substantiated.

## Preserved evidence

The entire **683-file / 160,102,888-byte** terminal result tree was copied and
SHA256-verified before inspection. Positive/negative calibration controls were
re-audited into a fresh control file. The result manifest SHA256 is
`f679dc6a58174103c23d427f15d5ed7a69085651e49ab714a0a9d9b98a6c26b8`.

The [sealed follow-up results](cloudnativepg-plan-54-rerun-results.json) include
the exact trigger count, native readiness-source proof, prior phase comparison,
strict physical/application evidence, raw alarms and preservation hashes.
Their SHA256 is
`81b1396f62675fcef62e23aa10de64b35b57b290c15f2e09de293ba9c30e1b69`.
The old campaign aggregate still has SHA256
`4ab56139c4836d82676427b6b085a612aa5b793699c7048c34049845344bac33`.
The new attempt adds four alarms to the 341 already retained; the prior
calibration's 28 alarms remain separate.

Evidence is in `runs/cloudnativepg-plan54-bounded-20261029-vm`, its fresh
`runs/cloudnativepg-plan54-bounded-20261029-review.json`, and
`.cache/cloudnativepg/plan54-followup-20261029/`. Existing campaign metadata was
archived before changing the current-job pointer; the original campaign state,
report, aggregate, manifests and reviews were hash-checked unchanged. The user's
dirty `tests/test_trace_errors.py` remains untouched and unstaged.
