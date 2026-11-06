Flux kustomize-controller v1.10.1 is pinned to
`746d9d2d360bbc7a5ee75ab79dadcf05e48ab927`. The profile preserves its native Go
1.27 minimum, client-go v0.37.1, controller-runtime v0.25.2 and SSA v0.83.0.
The image uses the upstream runtime packages and build flags. Reconciliation
uses Flux's native `--concurrent=10`; `num_workers: 0` selects zero additional
Kind worker **nodes**.

`prune-retry` serves two deterministic synthetic source archives from an
in-cluster HTTP fixture. It supplies a native GitRepository object and artifact
status directly; it does not run source-controller or access a Git credential.
The source CRDs come from source-controller v1.10.0, the version selected by the
pinned controller. OCIRepository and Bucket must also be installed because the
native controller watches them even when the workload uses GitRepository only.
ExternalArtifact is installed for the optional native watch. The Kustomization
CRD is copied from the pinned source. The vendored CRDs retain upstream content.

The first archive creates four ConfigMaps. The second retains only the anchor.
A ValidatingAdmissionPolicy rejects DELETE for the victim. The workload confirms
the policy through a server-side dry-run, then requires `Ready=False` with
`PruneFailed`, unchanged victim identity/data, and retained victim inventory.
After removing the policy, Flux must retry naturally, delete the victim, remove
its inventory entry, and become Ready at the second revision. The prune-disabled
ConfigMap and the ConfigMap with a foreign owner label must survive unchanged.
Each phase must satisfy the semantic monitor for three seconds, with a new
stability window after controller pod identity changes.

The source already contains failed-prune inventory retention (upstream #1665).
This experiment checks that behavior across a process interruption. It is not a
claim that the historical inventory-loss bug remains unfixed.

The native SSA `ResourceManager.Delete` method has observation callbacks at entry
and return. Reverse lexicographic pruning visits `zz-sieve-prune-victim` first.
The campaign fault restarts the controller at the first return callback, after
the rejected request and before the caller can merge survivors into inventory.
The test-image control uses the same image and workload with `actions: null`.
Qualification must establish callback coverage and ordering from fresh learning
traces before executing that plan.

The Nov. 2 campaign and its failures remain sealed in
`docs/rfe/managed-child-repair/kustomize-prune-reliability-20261102.md`.
The Nov. 3 completion fixed annotated-call analysis and passed five fresh native
workloads: vanilla, two learning executions, the matched test-image control and
one physically verified controller restart. Inventory retention, natural retry,
and both policy controls passed with zero raw alarms. See
`docs/rfe/managed-child-repair/kustomize-prune-completion-20261103.md` for the
placement proof, preserved evidence, budget accounting and scope limits.

Build and workload entry points are the public Sieve CLI:

```sh
sieve build -c kustomize-controller -m all -r LOCAL_REGISTRY
sieve vanilla -c kustomize-controller -w prune-retry -r LOCAL_REGISTRY --output FRESH_PATH
sieve learn -c kustomize-controller -w prune-retry -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root FRESH_ORACLES
sieve test -c kustomize-controller -p FROZEN_PLAN -r LOCAL_REGISTRY --output FRESH_PATH --oracle-root VERIFIED_ORACLES
```

The learn command with oracle construction executes two fresh workloads. The
Nov. 2 campaign has a seven-workload/two-fault cap, zero automatic retries, and a
confirmation pair reserved exclusively for a meaningful physically verified
failure. Run builds and native workloads only on the disposable Linux VM. Check
for active jobs before launching. Observe only completion markers and service
state during a stage; collect the entire completed result tree and verify all
files against VM SHA256 values before examining results.

The workload writes accepted API responses, all semantic observations, pod
identities, deterministic archives, policy checks and process receipts beside
its completion marker. Sieve differential alarms, semantic outcomes, scheduling
proofs and harness failures require separate adjudication. Bounded convergence
alone cannot establish a previously unknown defect.

`partial-apply-control` and `partial-apply-rejected` test cleanup after staged
application. Three deterministic archives establish an anchor, add a Namespace
and ConfigMap, then remove the additions. A matching admission policy uses
`Audit` for the control and `Deny` for the rejected case. Flux creates the
Namespace in its first native apply stage; admission rejects the later
ConfigMap stage in the rejected case. The workload records inventory, three
acknowledged reconciliations of the source-removal revision, and parent deletion
with `deletionPolicy: Delete`. The removal revision is acknowledged before the
policy is removed, preventing a successful retry of the expanded revision from
masking the inventory gap. No scheduling actions are used.

The workload continues collecting cleanup evidence after an inventory failure.
`partial-application.json` distinguishes process completion (`status`) from
semantic checks (`semantic_status`); a completed process can establish a failed
semantic check. Independent review checks the native logs and raw API samples,
including resource UIDs, acknowledged revisions, policy exclusions, and deletion
timestamps. Samples are separate API reads and may capture a normal retry in
progress, so phase qualification uses the full observation sequence.

The Nov. 3 partial-application campaign has a four-execution cap, including
failures, and zero scheduling faults. See
`docs/rfe/managed-child-repair/kustomize-partial-application-20261103.md` for
results, failed-attempt accounting, source history and sealed evidence.
