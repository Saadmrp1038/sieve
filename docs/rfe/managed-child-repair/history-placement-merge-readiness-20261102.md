# Merge readiness: bounded histories and verified overlap

This branch adds reproducible delivery overlap and a deterministic, bounded
history/placement explorer for External Secrets and cert-manager. The new catalog
contains sixteen cases: two operators, two metadata maps, two return histories and
two adjacent placements. Existing catalog entries remain supported. Every native
controller retains ten reconciliation workers.

## Standards review

One finding was resolved: overlap query commands now use an explicit artifact
working directory. Public workload CLI regressions for both operators failed with
the ambient working directory, then passed with the fix, including a path with
spaces. The follow-up found no remaining material standards issue.

## Specification review

One finding was resolved: an observer event rejected by coverage validation now
immediately invalidates the live overlap and releases its held RPC with the
original error. A public coordinator RPC regression reproduced the stranded
worker before the fix, then verified prompt release, retained failure diagnostics
and rejection of later attempts to revive the failed schedule. The follow-up
found no remaining material correctness issue. This is a harness reliability fix;
no previously unknown operator defect was established.

## Validation

The local Python suite passed 698 tests and 10 subtests. The protected
`tests/test_trace_errors.py` was excluded from local pytest and Ruff commands;
its preexisting contents remain unchanged and unstaged. Ruff lint/format, strict
Mypy across 47 source files, wheel build, actionlint, all three Go module race and
vet checks, and Python/Go dependency audits passed. The Go scanner reported no
affected called symbols or imported packages. It retained two existing module
advisories, GO-2026-6866 and GO-2026-6860, in `golang.org/x/net@v0.61.0`;
the coordinator dependency files are unchanged by this branch. The injected Go
client still has no third-party module dependencies. A CLI smoke check generated
all sixteen cases without starting a workload.

The [repeated native overlap campaign](reproducible-delivery-overlap-results-20261102.md)
verified four fault executions and two matched controls. The later
[remove/re-add pilot](history-placement-pilot-results-20261102.md) independently
verified two matched controls and two physical overlaps within eight workloads,
including four fresh learning executions. All semantic reviews passed. The
pilot covered two of the sixteen new cases: labels/remove-readd at the later
placement. Restore-value, annotations and the earlier placements remain untested
natively in the new catalog.

Those native results retain their frozen source/image bindings. The final
observer-error and command-directory review fixes were verified through public
RPC/CLI regressions; no native images were rebuilt and no further native
workloads or faults were launched during merge preparation.

All 89 retained raw result trees and nine evidence seals were reverified
unchanged. The protected test SHA256 remains
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`. New local check logs, red/green receipts and review details live
under `.cache/operator-sequences/history-placement-merge-ready-20261102/`.
The publication branch is `history-placement-explorer`.
