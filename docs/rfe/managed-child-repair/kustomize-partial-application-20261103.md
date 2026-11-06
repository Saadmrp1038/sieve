The partial-application experiment reproduced one kustomize-controller behavior
failure in two fresh native executions. A Namespace created before a later apply
stage failed was omitted from inventory. It survived removal from the source,
three acknowledged successful cleanup reconciliations, and deletion of its
Kustomization. The matched vanilla control tracked and deleted the Namespace.
This is an established failure in pinned v1.10.1, without a claim of novelty.

| Category | Result |
| --- | --- |
| Implementation | Added public `partial-apply-control` and `partial-apply-rejected` workloads, structured observations and three semantic checks. Native operator logic and dependencies are unchanged. |
| Verified coverage | One matched vanilla control and two fresh rejected-application cases completed. All use native `--concurrent=10`, identical source archives and the same qualified uninstrumented binary. |
| Operator failure | Both rejected cases omitted a successfully created Namespace from inventory and left that same live Namespace after source cleanup and parent finalization. |
| Harness failures | One blocked launch consumed a slot. Two local review assumptions were corrected using preserved raw evidence; neither required a native rerun. |
| Execution budget | Four charged slots: one blocked launch and three actual native workloads. Zero scheduling faults, zero controller restarts and zero automatic workload retries. |

The subject is kustomize-controller v1.10.1, commit
`746d9d2d360bbc7a5ee75ab79dadcf05e48ab927`. It runs on the previously qualified
Kubernetes v1.36.4 environment. The controller binary SHA256 is
`6e26fe6e7a27228bdd3fd69d28833f43393a8013eae43feba613f6c16d13e9d4`.
All 87 Sieve Go input files match the preceding qualification. Each observed
controller image resolves to the previously exported and verified OCI index
`sha256:7f4cbafd12b9db85f0a2fd0c47c606881ca88cbc6daec5c3761a19cf4bb38cca`.
Deployment arguments and native startup logs both establish ten reconciliation
workers. There were no controller Pod replacements or container restarts.

Three deterministic synthetic archives drive each workload. Revision 1 contains
only `ConfigMap/default/sieve-partial-anchor`. Revision 2 adds
`Namespace/sieve-partial-created` and
`ConfigMap/default/sieve-partial-blocked`. Revision 3 contains only the anchor
again. Initial and removal archives have the same content digest; their source
revision identifiers differ. All three archive hashes match across the control
and both rejected cases. A native GitRepository artifact status is supplied by
the workload and served through an in-cluster HTTP fixture. Source-controller
and other Flux operators are not running in this experiment.

A ValidatingAdmissionPolicy matches only CREATE and UPDATE of the blocked
ConfigMap in `default`. Its binding uses `Audit` for the control and `Deny` for
the rejected cases. An independent server-side dry-run confirms the intended
admission behavior before publishing revision 2. The policy never matches
Namespace deletion. Flux applies the Namespace in its earlier native stage,
then encounters the rejected ConfigMap dry-run in the later stage. The input
manifests themselves are valid; this does not use the obsolete `validation: none`
setting.

After observing the expanded revision, the workload publishes revision 3 and
waits for its Ready acknowledgement before removing the admission policy. This
ordering prevents a successful retry of revision 2 from repairing inventory and
masking the case. With the policy removed, the workload requests three further
reconciliations through distinct native `reconcile.fluxcd.io/requestedAt`
annotations. Each must acknowledge the request in `lastHandledReconcileAt`,
report Ready for the current generation, and retain `lastAppliedRevision` 3.
The workload then deletes the parent with `prune: true`,
`deletionPolicy: Delete`, and suspension disabled. It observes parent absence for
at least five seconds. There is no scheduling action, process interruption,
unsupported concurrent ownership, manual inventory patch or manual Namespace
creation/deletion by the workload.

| Observation | Control | Rejected case 1 | Rejected case 2 |
| --- | --- | --- | --- |
| Namespace in expanded inventory | Yes | No | No |
| Namespace absent after source cleanup | Yes | No | No |
| Namespace absent after parent finalization | Yes | No | No |
| Acknowledged extra cleanup reconciliations | 3 | 3 | 3 |
| Same leaked Namespace observed after parent absence | None | 6.38 seconds | 6.49 seconds |

The two leaked Namespace UIDs are
`ca782452-20a9-4b75-b2bc-01fb385e96b8` and
`5dc73834-8bab-4711-8ac4-6773dd4735b1`. Each stays unchanged through the entire
experiment, remains Active, and has no deletion timestamp. Both carry the
correct Kustomization owner labels and no prune, reconcile or SSA exclusion.
The standard Namespace `kubernetes` finalizer is present, but deletion was never
accepted: this is not a Namespace stuck terminating. The tracked anchor is
deleted when the parent finalizes, independently establishing that finalization
and cleanup of inventoried resources occurred. Parent UID, spec and generation
remain fixed until deletion.

The native log records Namespace creation in the same reconciliation error as
the later ConfigMap admission rejection. Seven qualified expanded-phase samples
in each rejected execution independently record the failed current revision.
Inventory contains only the original anchor. The same Namespace remains in API
observations after three successful revision-3 reconciliations and after the
parent disappears. Each run supplies 64 raw observation rows. The two failures
were obtained in separate fresh clusters, with different parent, Namespace and
controller Pod UIDs. All three processes and public Sieve CLIs exited 0 because
evidence collection completed; `semantic_status: failed` and three false checks
carry the operator outcome. These were vanilla executions, so no differential
Sieve oracle or coordinator scheduling proof was evaluated.

The causal source path is consistent with the physical observations. Pinned
[SSA staged apply](https://github.com/fluxcd/pkg/blob/ssa/v0.83.0/ssa/manager_apply.go#L439-L517)
creates cluster definitions first and returns their partial change set when a
later stage fails. The controller's
[apply error path](https://github.com/fluxcd/kustomize-controller/blob/746d9d2d360bbc7a5ee75ab79dadcf05e48ab927/internal/controller/kustomization_controller.go#L1038-L1055)
includes that change set in the error log but returns a nil change set. Its
[caller exits before inventory construction](https://github.com/fluxcd/kustomize-controller/blob/746d9d2d360bbc7a5ee75ab79dadcf05e48ab927/internal/controller/kustomization_controller.go#L499-L521).
Later pruning compares old and new inventories; finalization also uses the
inventory. The successfully created Namespace consequently has no inventory
entry through either cleanup opportunity. This is an inference supported by
the unchanged pinned source, creation log, persisted inventory and repeated
semantic observations; it does not rest on an alarm alone.

The related orphaning class is already known in
[upstream #409](https://github.com/fluxcd/kustomize-controller/issues/409).
That older `validation: none` report was considered fixed by #426's enforced
validation. This experiment demonstrates a staged-application variant with
validation enabled: the Namespace stage succeeds before a later stage's
validation/admission rejection. The release also already contains
[#1665](https://github.com/fluxcd/kustomize-controller/pull/1665), which retains
inventory after failed pruning; that fix handles a different path after
successful application. Current main at
[`e31c6fed2d4557a322fbe4dbe4976f52e7b8ac91`](https://github.com/fluxcd/kustomize-controller/blob/e31c6fed2d4557a322fbe4dbe4976f52e7b8ac91/internal/controller/kustomization_controller.go)
was reviewed and retains the relevant error/inventory ordering. Current main
was not executed. A previously unknown defect is not claimed.

The experiment was reported in
[upstream issue #1803](https://github.com/fluxcd/kustomize-controller/issues/1803).
A maintainer acknowledged the behavior as an issue and applied the `bug` label.
This repository does not include a Flux fix.

The implementation used the preapproved public workload CLI seam and the
repository TDD skill. The first test failed because the new workload was not
accepted, then passed after implementation. External Kubernetes/process
fixtures cover control success, retention of partial inventory, and lost
inventory with cleanup evidence still collected. Final focused validation
passed 38 tests, Ruff check and formatting, strict mypy for 47 source files, and
the allowed diff check. The protected dirty `tests/test_trace_errors.py` was
excluded from pytest and Ruff and remains at SHA256
`b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.

The initial snapshot rsync connection was reset. A launch was attempted before
upload success was confirmed; the immutable-input preflight refused the missing
archive before systemd or the workload started. The failed launch, incomplete
remote preparation and all frozen version-1 inputs are retained. It was
conservatively charged one of the four slots. Preparation used a fresh version-2
remote path, followed by a fresh control and the two rejected cases. The
original launch was not retried. There was therefore only one actual control,
rather than the originally proposed two, and zero failed native harness runs.

The first local review expected `managedFields`, which kubectl omits by default.
Its error and script remain preserved; the revision uses native creation logs,
API identity and frozen workload inputs instead. A second review found that a
supplementary phase sample caught the beginning of an ordinary retry with
Ready=Unknown. The full observation sequence contains the qualified failed
samples. The final common reviewer uses that sequence, handles either semantic
outcome, and verifies image identity, policy exclusions, parent identity and
the post-finalization window. The reviewer was frozen before the second
rejected result was reviewed. Raw samples and native outputs were never edited.

Evidence is indexed at
`.cache/kustomize/partial-application-20261103/evidence-index.json`, with
`results.json`, `ledger-final.json`, the three `*-independent-final.json`
reviews, `history-review.json`, and `seal.json` beside it. Whole VM result trees
are `runs/kustomize-partial-{control-1,control-2,rejected-1,rejected-2}-20261103-vm`;
`control-1` records the blocked launch. During each running stage, VM inspection
was limited to its completion marker and service state. Every completed result
tree was copied in full and verified against VM SHA256 values before review.
All twelve prior campaign VM trees were also reverified. Earlier reports,
images, raw logs, failed attempts, frozen inputs and seals remain intact.

The VM is idle, owned test clusters are gone, and all six previously qualified
controller/node images remain retained. No credentials were inspected or
copied; no generative evaluation, local Docker, native image build, image
pruning, operator fix, commit, push or PR occurred. The changes remain
uncommitted on `kustomize-prune-reliability`. Coverage is bounded to this
synthetic staged-application scenario in pinned v1.10.1.
