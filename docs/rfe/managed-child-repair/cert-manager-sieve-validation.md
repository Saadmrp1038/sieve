# cert-manager instrumented Secret repair validation

October 27, 2026 (America/Chicago; VM artifacts use UTC dates).

Sieve generated and attempted **49 unchanged plans** against cert-manager
v1.21.3. **20 controller restarts were physically verified.** Fifteen restart
triggers did not fire. None of the 14 read-pause schedules completed: ten held
reads without releasing them and four never held a read. **39 workloads repaired
the managed Secret; ten timed out under an unreleased pause.** No operator bug
was substantiated. This is not a 49/49 pass or verified missed-state coverage.

## Counts and physical evidence

Both published-image vanilla controls and all four instrumented learning passes
passed. Each workload learned two fresh baselines before generating plans.

| Workload | Restart plans attempted | Verified restarts | Pause plans attempted | Completed pauses | Successful repairs | Raw alarms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `delete-child` | 18 | 10 | 7 | 0 | 20 | 39 |
| `drift-child` | 17 | 10 | 7 | 0 | 19 | 38 |
| Total | 35 | 20 | 14 | 0 | 39 | 77 |

The original campaign finalized cases 0–15, then its disk-space guard stopped
execution before case 16. A continuation ran cases 16–48 using the same images,
oracles and exact generated YAML bytes. There were 49 distinct fault attempts;
controls and learning passes are excluded from those counts. No schedules were
amended and no new plans were generated for the continuation. There were zero
stale-state/API-pause/reconnect plans and zero per-plan harness exceptions.

Each verified restart has an old-pod DELETED event in the actual API log, a
distinct replacement UID, a Ready nonterminating replacement in the final pod
snapshot, a completed coordinator and a successful injection-completion process
outcome. Eighteen of these cases had zero raw alarms; two retained six alarms
each. Physical injection success and oracle success are reported separately.

All 39 completed repairs have accepted UID-tested mutations, unchanged parent
UID/generation/spec, current-generation Ready, Certificate ownership, matching
certificate/private-key public keys, valid hostname/server TLS material and
15 seconds of stable repair. Deletion additionally requires a new Secret UID.
The independent audit rechecked TLS using OpenSSL and compared direct workload
observations with API events and saved state.

All 49 test states and four learning states match independent reconstruction
from their API logs, capture `configmap/default/kube-root-ca.crt` and have the
expected `v1.36.4-sieve-test` or `v1.36.4-sieve-learn` API version. Learning logs
contain informer receipts, cache reads, writes and reconciliation observations.
No operator or coordinator panic was found in the captured logs.

## Incomplete injections

Case indices below are zero-based indices from the original 49-plan selection.

Fifteen restart cases never deleted the controller pod and reached the existing
600-second post-workload injection deadline. Their workloads repaired the Secret,
but they provide no restart coverage:

- Deletion cases: 2, 4, 6, 9, 10, 11, 15 and 17.
- Drift cases: 27, 30, 33, 34, 35, 39 and 41.

Twelve of these schedules contain literal generated private-key Secret names
from learning, either as resource keys or inside `nextPrivateKeySecretName`
predicates. The corresponding names in the actual test API events differ.
Two readiness-status predicates (10 and 34) and one second Secret-write predicate
(4) also did not match; their exact reachability cause remains unisolated. These
three cases remain an explicit coverage limitation.

All fourteen pause plans failed to establish a completed missed-state injection:

| Pause outcome | Cases | Workload outcome |
| --- | --- | --- |
| Issuer initial read held, no release | 18, 42 | SelfSigned Issuer readiness timed out |
| Certificate initial read held, no release | 19, 43 | Initial convergence timed out |
| Secret initial read held, no release | 21, 45 | Initial convergence timed out |
| Certificate revision read held, no release | 22, 46 | Initial convergence timed out |
| Certificate validity-field read held, no release | 23, 47 | Accepted mutation; child repair timed out |
| Pause trigger never held a read | 20, 24, 44, 48 | Workload repaired; injection incomplete |

The four unheld pauses also contain literal generated Secret names absent from
their test executions. The ten held pauses have matching coordinator messages
inside the blocking read RPC, and no corresponding release. Their resume needs
a later update/deletion or field modification that the paused reconciliation
and this workload cannot independently supply. Eight stop before the workload
mutation; two stop repair after an accepted mutation with an unchanged parent.
These are circular schedules for this workload/profile, not evidence of failure
to recover after a completed missed-state fault. No read hold was released by
a timeout, and no intermediate-state miss is claimed.

The generator, pruning, trigger matching, action semantics and oracle verdicts
were not changed to make these schedules pass.

## Every raw alarm retained

All **77 raw alarms** remain in the original verdict files and the separate
review JSON:

- **65 alarms** belong to the ten unreleased pauses: ten workload timeouts and
  55 resulting state discrepancies. They describe the intentionally blocked
  initial convergence or repair, not a semantic failure after fault release.
- **12 alarms** belong to effective restart cases 13 and 37, six each. They
  concern CertificateRequest 1 versus 2, Certificate revision 1 versus 2, retained
  request history and fewer temporary Secret events.

In both restart cases, `parent_before` had `Ready=True` and `Issuing=True`, an
allocated next private-key Secret and no recorded revision. The mutation began
while the initial issuance status was still being committed. Restart interrupted
the interval after writing the first TLS Secret but before recording its
revision. Recovery used the existing signed request and key, and finished at
revision 1. The baseline instead completed initial issuance before mutation and
finished at revision 2. The deleted Secret was recreated with a new UID; the
drifted Secret's data was restored. Both ended Ready, owned, stable and with valid
matching TLS data, preserving the original certificate bytes.

This is consistent with the pinned
[issuing controller's write order](https://github.com/cert-manager/cert-manager/blob/925c22283de556172d48d96a165dc2fa27f7a6ff/pkg/controller/certificates/issuing/issuing_controller.go):
`issueCertificate` writes Secret data before setting `status.revision` and
removing the Issuing condition. Revision/history differences alone do not
establish an operator bug here. The workload's initial healthy check does not
require issuance to be quiescent. That is an evaluation limitation, not grounds
to remove the alarms or claim oracle success. No alarm was suppressed or masked.

## Subject and source provenance

The profile pins cert-manager **v1.21.3**, commit
`925c22283de556172d48d96a165dc2fa27f7a6ff`, retaining its native Go 1.26.10,
client-go v0.36.2 and controller-runtime v0.24.1. The upstream Containerfile,
licenses, pinned base image, default five workers per controller and all default
controllers/issuer registrations are retained. All **927 original operator Go
files are byte-identical in the test build**. Learning changes only observation
calls/import formatting in seven controller files.

Learning observes six distinct Certificate ProcessItem frames (trigger,
keymanager, requestmanager, issuing, readiness and revisionmanager) and the
namespaced Issuer frame. Shared CertificateRequest issuer/approver methods are
not annotated; those controllers stay enabled and their API events are captured.
This limits scheduling coverage of their internal operations. The existing
single-active-worker-per-annotated-frame trace limitation remains; the workloads
create one Certificate and do not evaluate concurrent Certificates.

The profile moves the main controller to `default`, adapts its service account,
binding subjects and metrics Service, and retains cluster-resource namespace
`cert-manager`. Official webhook/cainjector run in `cert-manager`. The supported
nondefault `--enable-certificate-owner-ref=true` policy enables the ownership
check. Workloads use a SelfSigned Issuer and ECDSA Certificate; no external issuer
or production credentials are involved.

Builds used profile commit `3232d7305228564fb6be29060f73bbdd8368c515`.
Campaign runtime sources match all 142 manifest files at host-only loader fix
`798b2d15f5c4a349c7a29321b71725886cea075c`. The continuation verified unchanged
image IDs and oracle/plan hashes. Source patches, native compiler/module build
information and original-file checks are retained in both result directories.

Docker's reported image IDs in registry `sieve-cert-manager-20261027`:

| Image | `docker image inspect` ID |
| --- | --- |
| `cert-manager:learn` | `sha256:d4e21ab5ed6085aaad1caf82d099345334b1bdd89925e15e9876512a430725f3` |
| `cert-manager:test` | `sha256:490a194b757d8397650b8cc61bf7cd5add9ccc80ce48356d8d0d49f064641575` |
| `node:v1.36.4-learn` | `sha256:71ffe52dd2fbd3e47f80dbc1a7db2f39710a80fdf337a4df7e456b50f8823cd6` |
| `node:v1.36.4-test` | `sha256:99809e4f9e76d3606a53dfc575a047580e147a7561188d05603ba86ad7eb1399` |

Nodes reuse verified upstream Kubernetes 1.36.4 components with only
kube-apiserver rebuilt for Sieve; no node rebuild was needed for this profile.
The published vanilla control's release index is
`sha256:9f4f96a33c7774cef24466fcd85031997531a03a8d117f151126baa8d1290dcc`;
its AMD64 manifest is
`sha256:9b262e9493ab3e0daebc2c9cfbddc6df68b9ac91afc036a759584f3d49d607e5`.
The release deployment manifest is checked against
`sha256:c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74`.

## Setup failures and continuation

The first two attempts executed zero plans. Docker/kind image import first
requested an absent ARM sibling manifest, then exposed missing AMD64 config/layer
blobs in the source image store. Normal Docker pull incorrectly reported the
incomplete local image as up to date. Eighteen original OCI blobs were downloaded
by immutable AMD64 digest with checksum-verified crane v0.22.1 and empty auth
configuration, hash-verified and imported. No binary was rebuilt or modified for
the vanilla control.

Commit `798b2d1` adds a host loader fallback: retain direct kind loading, pull
only an absent local image, and otherwise export/import an archive containing
the actual node platform. A public setup/command regression failed before the
fix and passed afterwards. Export/import failures propagate, temporary files
are removed and all commands retain finite budgets. Fault behavior is unchanged.

After case 15, the unchanged 2 GiB disk floor stopped the next fresh cluster.
Hash-verified relocation of disposable download/tool/Go caches to the existing
private executable RAM volume recovered 565 MiB. Original paths are symlinks;
raw results and container images were neither moved nor removed. Both controller
images and all node images were reused. The continuation selected exactly the
33 unattempted YAMLs and copied the existing oracle unchanged.

The campaign ran from 20:52 UTC October 27 to 02:53 UTC October 28, including the
brief capacity-recovery gap. Services ran as `Pial` with an eight-hour cap,
1,500-second CLI phases and the existing 600-second cluster/injection deadline.
Untriggered schedules consume that finite wait; none is counted as successful.

## Commands, validation and evidence

Recorded VM entrypoints, each with a fresh output path:

```sh
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/cert-manager/run-retry2-vm.py
/users/Pial/sieve-ci-20261026/.venv/bin/python \
  /users/Pial/sieve-ci-20261026/.cache/cert-manager/run-resume-vm.py
```

Each result directory retains its exact runner as `run-vm.py`. Its summary and
command journals record exact `sieve vanilla`, `sieve learn --build-oracle` and
`sieve test -p PLAN` arguments, output/oracle paths, deadlines and outcomes.
Scripts embed unique paths; repeating a campaign requires new output paths.

Local evidence:

- `runs/cert-manager-sieve-vm/`: 77 files from the initial build/setup attempt.
- `runs/cert-manager-sieve-retry1-vm/`: 72 files from the second setup attempt.
- `runs/cert-manager-sieve-retry2-vm/`: 5,699 files, including both controls,
  four learning passes, all 49 generated plans, oracles and the first 16 tests.
- `runs/cert-manager-sieve-resume-vm/`: 12,394 files for the remaining 33 tests,
  copied oracles/plans and image/source/capacity provenance.
- `runs/cert-manager-sieve-review.json`: independent per-case physical,
  semantic, state-reconstruction and raw-alarm audit, separate from raw verdicts.
- `runs/cert-manager-sieve-audit.py`: retained read-only review script.

Every file in the four final result directories was SHA256-verified against its
VM counterpart. Manifests remain under `.cache/cert-manager/` as
`initial-result-hashes.json`, `retry1-result-hashes.json`,
`retry2-result-hashes.json` and `resume-result-hashes.json`. The audit does not
rewrite raw results or generated plans.

Local validation exercised 325 tests plus ten subtests: a full 323-test suite
passed before two additional image-loader fast-path cases, then all six final
image-loader cases passed. Ruff, strict Mypy, workload typing and wheel packaging
passed. All three Go modules passed race/vet checks, including public native
client-go v0.36.2/v0.37.1 cache operations and actual held/released reads. Go did
not change for the loader fix. The injected client remains standard-library-only.
Frozen trace, plan and verdict contracts pass.

## Remaining limits

The useful verified fault coverage is 20 restart schedules for this pinned
SelfSigned profile, not all 49 attempts. Missed-state coverage is zero, and no
stale-state plans were generated. Literal generated-name predicates, circular
resume dependencies, three further unmatched restart predicates and the initial
issuance-quiescence gap remain visible limitations. Shared method frames,
multiple Certificates, other issuer types, webhook/cainjector faults and other
operator versions are outside this validation. No operator logic was changed,
and no upstream bug or PR was published.
