# Secret Generator acceptance schedules

These twelve files are byte-for-byte copies of the schedules replayed by the
verified oracle-refactor batch at Sieve commit
ac59ea4c1de0c1851a0f23634680dd59f68d56c8. `suite.json` records their SHA-256
hashes and the pinned controller/Kubernetes versions. Five schedules restart the
controller; seven make it miss an intermediate state by holding a read.

The two-case smoke selection uses restart plan 1 and missed-state plan 7. Plan 7
retains the previously reviewed Secret-reconciler `pauseBy` amendment. This lets
the StringSecret reconciler recreate the Secret required by the resume trigger.
The amendment predates this CI work; no fault schedule was changed here.

Fresh learning still runs twice. Acceptance replays these frozen schedules
against the fresh reference, rather than replacing them with newly generated
plans whose identities may vary. Do not refresh hashes or schedules simply to
make a failing run pass; review any behavior change separately.
