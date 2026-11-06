Flux kustomize-controller pruning behaved correctly in two fresh learning
workloads and a matched test-image control. Both denied-delete inventory retention
and recovery passed independent API checks. The control produced zero Sieve
alarms. This campaign did **not** validate a scheduling fault or complete a
successful vanilla qualification. Five workload attempts were consumed, including
two harness failures; the conditional confirmation reserve remains unused.

| Category | Established status |
| --- | --- |
| Implementation | New controller profile, build/deploy assets, complete native source CRDs, deterministic HTTP artifact fixture, prune-retry workload, public semantic monitor, and regression tests are present. The instrumenter now knows the `go-digest` package name. |
| Verified operator coverage | Three completed synthetic pruning workloads: two fresh learning runs and the identical test-image control with `actions: null`. Native logs and deployment snapshots confirm ten workers through `--concurrent=10`. |
| Harness limitations | Two workload attempts failed during setup. Fresh learning then completed both workloads but automatic graph/plan generation failed on Sieve's unsupported annotated-call range semantics. That guard remains intact. |
| Independently established operator failures | None. Deliberate admission refusal is an input to the experiment, not a defect. No previously unknown defect is claimed. |
| Scheduling coverage | No scheduling-fault attempt, restart, or physical restart proof. Callback placement was observed; a planned placement is not an executed fault. |

The subject is [v1.10.1 at commit
746d9d2d360bbc7a5ee75ab79dadcf05e48ab927](https://github.com/fluxcd/kustomize-controller/tree/746d9d2d360bbc7a5ee75ab79dadcf05e48ab927).
The source already includes failed-prune inventory retention from
[upstream #1665](https://github.com/fluxcd/kustomize-controller/pull/1665), introduced
by `ab4ec075a97f2790c7d02c48021ea81086da5964`. The source-history review also
identified intentional foreign-owner pruning exclusion in
`d0222867e69342b85c8d69bb272755c439c9bfb4`. The pinned
[SSA delete implementation](https://github.com/fluxcd/pkg/blob/ssa/v0.83.0/ssa/manager_delete.go)
checks live owner labels and prune exclusions. These existing fixes and policies
are the basis of the expected results.

The proposed fault hypothesis was that failed-delete inventory retention should
survive a controller restart after the first rejected `ResourceManager.Delete`
returns but before its caller merges survivors into inventory. The executed
portion tested rejection and recovery without that restart. A synthetic
GitRepository status points to two deterministic, in-cluster HTTP archives. No
source-controller process, Git credential, or real application resource is used.
The first archive creates an anchor, a victim, a prune-disabled ConfigMap and a
ConfigMap subsequently given a foreign owner label. The second archive retains
only the anchor. A ValidatingAdmissionPolicy rejects deletion of the victim.

Independent checks of the saved API responses establish:

- Initial inventory contains exactly the four expected ConfigMaps.
- With current-revision `Ready=False/PruneFailed`, inventory contains exactly the
  anchor and victim. The victim retains its UID, data and absent deletion timestamp.
  The status message identifies the intentional admission denial.
- After removing the policy binding and policy, native retry converges without
  an explicit reconcile request. The victim is absent, inventory contains only the
  anchor, and Ready acknowledges the second applied revision.
- The prune-disabled and foreign-label controls retain their original UIDs and
  synthetic values. Each semantic phase remains satisfied for at least three
  seconds. Identical artifact hashes bind all three completed workloads.

The foreign-label resource is a policy control; it is not a full ownership
handoff between two running Kustomizations. Only one Kustomization is exercised.

| Attempt | Image mode | Workload outcome | Harness outcome |
| --- | --- | --- | --- |
| 1 | Vanilla | Incomplete; pruning never reached | Deployment omitted OCIRepository and Bucket CRDs required by native watches. Added pinned source CRDs and a CLI configuration regression. No vanilla retry. |
| 2 | Learn | Initial four ConfigMaps became Ready; pruning never reached | Dry-run probe used unsupported `kubectl delete -o json`. Changed to `-o name`, covered by a complete public CLI fixture. The second learning pass never started and is not counted as an execution. |
| 3 | Learn, fresh | Passed | First completed baseline retained as `learn_prev`. |
| 4 | Learn, fresh | Passed | Comparison oracles were built from both completed baselines. Subsequent graph assembly failed closed with `annotated writes within reconciliation have no supported range semantics`. CLI exit was 1; no generated graph or plan is qualified. |
| 5 | Test, matched control | Passed | CLI exit 0, no exception, zero raw alarms, no triggered actions. |

The completed learning traces contain 10 and 13 matched SSA callback pairs,
respectively, with no observer identity or RPC errors. In both traces the first
pair reads the intended `zz-sieve-prune-victim`. Native status and API observations
confirm the rejected prune. These raw observations qualify that callback site;
they do not remove the graph limitation or establish a physically executed
schedule. The control's generic fault proof correctly reports no physical proof
for its empty action sequence; its semantic verdict comes from the independent
API review.

Image qualification preserved the native Go logic and dependencies. All 78
operator Go files and 68 SSA files were compared; the instrumented files are AST
equivalent after removing only the exact observation callbacks and import.
All 257 declared native module pins remain unchanged, including client-go
v0.37.1, controller-runtime v0.25.2 and SSA v0.83.0. Go 1.27.3 satisfies the native
Go 1.27 minimum. Packaged binary SHA256 values were checked in each image export,
and observed learning/test pod image IDs match the saved OCI index SHA256 values.
The existing immutable Kubernetes 1.36.4 observation images were reused with
recorded provenance; Kubernetes was not rebuilt for this campaign.

Two additional preparation issues and the build failures are preserved separately:
archive extraction rejected a preexisting CSI symlink; one SSH transfer reset;
the first learning build exposed the instrumenter's incorrect `go-digest` alias;
and a final host-side Alpine tag lookup failed after both corrected images had
already built. BuildKit's resolved base digest, image exports, failed source
checkout, raw command logs and independent qualification remain available. The
base-tag bookkeeping failure was not treated as an operator failure or hidden by
another build.

TDD evidence covers the public semantic-monitor interface, public workload CLI
with external Kubernetes/process fixtures, and public Sieve build CLI with a real
Go compiler and external Docker fixture. The full local suite passed 709 tests
and 10 subtests with the protected file excluded, before the final CLI regressions.
The final targeted semantic/workload suite passed 48 tests. The real-Go alias
regression, instrumenter race tests, Ruff and pruning-monitor mypy check passed.
The later checks did not rerun the full suite.

The tracked trees of base HEAD `f6c3c1365b70fd78da7e4fa47f26c1edc03ec435` and
`origin/main` were identical before changes: this branch retained 13 individual
commits while main had the corresponding squash. No branch merge, commit, push or
PR was made. Changes remain on `kustomize-prune-reliability`.
The protected dirty `tests/test_trace_errors.py` was neither modified nor staged;
its SHA256 remains
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The prior evidence index still matches its frozen SHA256.

All running-stage VM observations were limited to service state and completion
markers. Every completed VM result tree was copied in full and matched against
VM SHA256 values before review. The VM has no active Sieve service or running
container, and the qualified images remain present. No local Docker, image
pruning, user API/account credential inspection or copying, generative evaluation,
push or PR occurred.

The local evidence entry point is
`.cache/kustomize/prune-ownership-20261102/evidence-index.json`; machine-readable
outcomes are in `results.json`, the final accounting is in `ledger-final.json`,
and the seal is `seal.json` in the same directory. The seven collected VM result
trees are under `runs/kustomize-prune-*-20261102-vm`. The campaign directory and these trees retain the failed
preparations/builds, frozen runtime inputs, images, binaries, raw logs, oracle
alarms and independent reviews. The two-workload/one-fault confirmation pair was
not eligible for use because no meaningful physically verified operator failure
was established.
