# CloudNativePG harness fixes: focused review

October 29, 2026 (America/Chicago).

The focused review covers the admission-retry fix `94f5a0e` and initial-child
wait fix `40d9999`, comparing merged base
`45653882d6213b6f0d03faaba1bf3a3e03ffde83` with reviewed head
`34c2054963d15bffca8ba60ec283109279173f16`. Separate Standards and Spec reviews
found the same P2 deadline issue. Both remediation reviews have no unresolved
findings. No other actionable findings were identified.

## Standards

`docs/process-execution.md` requires nested operations to share the earlier
enclosing deadline. The initial-child wait only checked its clock before each
predicate and slept for the full polling interval. Its Service Get retained the
independent 30-second request budget even with `--ready-timeout 1`.

The fix encloses readiness predicates in the existing process deadline, rejects
responses arriving after expiry, and caps sleeps to the remaining budget.
Request timeouts occurring before the readiness deadline still propagate.
Expired waits remain setup failures with command logs preserved.

**Standards result:** one P2 finding, fixed; no unresolved findings or additional
actionable baseline smells.

## Spec

The initial-child fix promises an existence wait within the existing readiness
timeout before strict ownership verification. The original implementation
could exceed that budget and accept a late child response. Public workload CLI
regressions demonstrated both paths before their respective fixes:

- A Service response delayed three seconds with a one-second readiness budget
  previously allowed the workload to succeed. It now fails before deletion or
  any A/B/C/D mutation and writes no successful semantic outcome.
- A ten-second polling interval with a one-second readiness budget previously
  exceeded the regression's six-second subprocess cap. It now exits with the
  explicit initial-Service timeout within that cap, without mutations.

The existing delayed-child success, missing/foreign ownership failures,
admission recovery, policy-denial and exhausted-retry controls still pass.
No reconciliation wait was added between accepted updates. UID guards, YAMLs,
trigger occurrences, ten-worker configuration, oracles and native images remain
unchanged.

**Spec result:** one P2 finding, fixed; no unresolved findings. This is the same
finding reported by Standards, rather than a second distinct defect.

## Evidence and validation

The review reverified every file in the preserved original, diagnostic and
plan-54 follow-up result trees, including the zero-plan launch failure:
**44,813 files / 2,943,896,541 bytes** matched their existing manifests. All
16 rejected admission request logs from VM cases 60 and 62 retain their original
hashes, empty stdout and exit status 1. Their accepted mutations and completed
application results remain recorded separately from rejected requests.

The original 77-plan aggregate still records 74 physical effects, 75 application
passes and two unknown original outcomes. Diagnostic recoveries and the later
plan-54 pass remain separate. All 345 raw alarms across those attempts remain
preserved; no historical result, review, manifest or report was rewritten.

Validation logs and retained public-boundary test artifacts are in the fresh
`.cache/cloudnativepg/harness-focused-review-20261029/` directory. It includes
both RED/GREEN pairs, the two review conclusions and full evidence verification.
Final checks passed: **400 Python tests plus 10 subtests**, all **17 CloudNativePG
workload tests**, Ruff lint/format excluding the untouched user edit, configured
strict Mypy on 38 source files, strict workload typing with `MYPYPATH=src`, and a
wheel build. The 177 pre-review preservation hashes still match, including the
user's dirty test. Exact results and log hashes are recorded in `validation.json`
in that directory.

This review makes no new VM validation claim for the deadline correction.
Existing VM evidence validates the two original fixes; the new failure paths
are tested through the approved public workload CLI/external Kubernetes seam.
No controller/API/image rebuild, generation, relearning or generative evaluation
was performed. Existing evidence and the user's dirty
`tests/test_trace_errors.py` remain untouched. No push or PR was created.
