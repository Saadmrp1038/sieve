# cert-manager injection fixes: final VM validation

All **29 mapped replacement targets are physically verified**: 15 controller
restarts and 14 missed intermediate states. All final repair invariants passed.
No reproducible cert-manager bug was substantiated. Original YAMLs, unsuccessful
attempts and every raw alarm remain retained.

These replace the 15 untriggered restarts and 14 incomplete pauses in the
[initial campaign](cert-manager-sieve-validation.md). The predicates and pause
workloads changed explicitly; this does not claim that the original failed YAMLs
now execute unchanged.

## Counts and outcomes

| Execution | Attempts | Verified restarts | Verified misses | Healthy repairs | Raw alarms |
| --- | ---: | ---: | ---: | ---: | ---: |
| First replacement batch | 29 | 15 | 12 | 29 | 24 |
| Two Secret coverage retries | 2 | 0 | 2 | 2 | 0 |
| Two parent alarm replays | 2 | 0 | 2 | 2 | 6 |
| All attempts, including diagnostics | 33 | 15 | 16 | 33 | 30 |

The final unique target count is **15 restarts + 14 misses = 29**, excluding
replays. The two original unsuccessful Secret attempts are preserved. Eight
published-image vanilla controls and sixteen learning passes also passed.
Learning produced 284 candidates (230 restart, 54 pause); 29 distinct replacements
were selected. The other 255 candidates were not tested in this campaign.

The earlier 20 verified restarts remain separate evidence. Do not count failed
attempts, replacement schedules or diagnostic replays as additional unchanged
original-plan coverage.

## Changes and TDD

- `d0a7c05`: update predicates without state differences now honor occurrence
  counts. A public coordinator RPC regression failed before the fix.
- `a5de2fc`: wait for initial issuance to clear `Issuing=True` before mutation;
  `Ready=True` alone had accepted unfinished issuance. Public workload CLI red/green.
- `2e88e76`: opt-in successful-write occurrence predicates and explicit generated
  name prefixes. Public generation, REST-hook RPC and coordinator RPC red/green.
- `d3784fc`: independent Issuer/Certificate/Secret updates followed by the existing
  deletion/drift repair workloads. Public CLI red/green, including UID rejection.
- `f1cbe19`: preserve case-sensitive Secret/ConfigMap `data` keys during API-form
  conversion. The two Secret plans had expected `Tls.crt` instead of actual
  `tls.crt`. `TestConvertedSecretDataKeysArmTheReadHold` failed before the fix;
  the full coordinator race/vet suite passed afterward. A differently cased key
  cannot arm the hold. The same generated YAMLs then verified both misses.

The first four changes passed 340 Python tests plus 10 subtests, Ruff lint,
configured strict Mypy, workload typing, wheel build and all three Go race/vet
suites. The final host-only fix passed the full coordinator race/vet suite.
The pre-existing user edit in `tests/test_trace_errors.py` was untouched; its
extra blank line remains the sole full-tree formatting failure.

## Schedule provenance

Restart predicates count successful operations by reconciler/category/resource
before candidate pruning, merging update verbs as the runtime does. Only this
profile opts in. Temporary names use explicit `resourceKeyPattern: true` with
`secret/default/sieve-certificate-*`; resource and namespace remain exact.
Other profiles retain their existing literal/state-difference behavior.

The original restart candidates were first regenerated identically using legacy
predicates. Replacement candidates came from fresh, quiescent learning and retain
the same write category, reconciler and resource selector. Original indices
4, 10 and 34 needed occurrence amendments of 2→3, 1→2 and 1→2 respectively, using
observed fresh candidates. Every old/new predicate and SHA256 is recorded in
`runs/cert-manager-injection-fixes-vm/replacement-mapping.json` (SHA256
`2cd694128d7cbd4f53e438383594acfe8b4e0dabf23666ab27803b1811759f81`).

The 14 pauses use independent, UID-tested A/B/C/D API writes eight seconds apart,
without reconciliation waits between writes: SelfSigned CRL URLs, declared DNS
names, or corrupted/restored `tls.crt`. One baseline-observed reader is held;
receipts of the independent overwrite release it. This replaces the circular
controller-produced resume dependency. Original deletion/drift repair follows
convergence under the final declared parent. No timeout release, forced trigger,
changed pruning, proof relaxation or alarm suppression was used.

Original indices below refer to the initial 49-plan campaign. Replacement plan
numbers belong to the listed workload's generated family. Complete paths,
predicates, hashes and effective result directories are retained in the mapping
and `runs/cert-manager-injection-fixes-final-review.json`.

| Original indices | Original workload | Verified replacement plans |
| --- | --- | --- |
| 2, 4, 6, 9, 10, 11, 15, 17 | delete-child | restart 11, 13, 15, 18, 8, 3, 7, 9 respectively |
| 27, 30, 33, 34, 35, 39, 41 | drift-child | restart 11, 14, 17, 8, 3, 7, 9 respectively |
| 18 / 42 | delete-child / drift-child | corresponding `miss-issuer-updates`, pause 1 |
| 19, 20, 22, 23, 24 / 43, 44, 46, 47, 48 | delete-child / drift-child | corresponding `miss-parent-updates`, pauses 1, 10, 11, 12, 2 |
| 21 / 45 | delete-child / drift-child | corresponding `miss-child-updates`, pause 2; verified in retry |

## Physical and semantic verification

Every restart has a matching action, an old controller Pod **DELETED in actual
API events**, a distinct replacement UID, Ready/nonterminating replacement in the
final snapshot, completed coordinator and completed injection process.

Every miss has a real matching read hold/release, accepted intermediate and later
receipts in each observed cache stream, no intermediate Get by the paused
consumer, and a held Get returning an independently accepted later value for the
same UID/resourceVersion. The repaired Secret-plan retries returned C at resource
versions 808 and 820. Their YAMLs, images and oracles were unchanged.

The independent audit reconstructed every learned/test final state from raw API
events and compared it with saved state. All 49 learned/test states (16 learning,
33 test attempts) retain CA capture and expected API versions. All workload
processes completed, with no harness exceptions or operator/coordinator panics.

Accepted mutations and all four independent writes were checked against actual
API objects. Certificate UID was retained; only declared parent-mode patches
advanced generation/spec. Repair retained the final parent UID/generation/spec,
current-generation Ready, ownership, matching certificate/key, valid server TLS,
exact declared DNS names and 15-second stability. Deletion required an actual
old-Secret deletion and new UID. Issuer-mode repaired certificates contain the
final D CRL URL.

## All alarms and bug classification

Eight parent-update pause cases (original indices 19, 20, 23, 24, 43, 44, 47, 48)
produced three raw alarms each: reference CertificateRequest revision 6 missing,
actual revision 5 present instead, and Certificate status revision 5 versus 6.
Two exact-YAML replays (original 19 and 43) reproduced the same three strings each.
All 30 alarm records remain unchanged in raw results and audit reports.

The observed difference is one fewer intermediate issuance under the deliberately
missed reconciliation. Every final declared input, certificate and repair
invariant passed, including the replays. This is an issuance-history difference,
not a substantiated final-state operator bug. Raw Sieve oracle failures were not
turned into passing verdicts or masked away.

## Runtime and retained evidence

Subject: cert-manager v1.21.3 at
`925c22283de556172d48d96a165dc2fa27f7a6ff`, native Go 1.26.10,
client-go 0.36.2 and controller-runtime 0.24.1. Operator logic, default five workers,
controllers and issuer/provider registrations remain unchanged. All 927 original
Go files were byte-identical in the test build; learning changed only seven
observational files. Existing namespace adaptations and supported owner-ref flag
remain as documented in the profile.

Runtime manifests verify 146 committed files at `d3784fc` for the first batch and
`f1cbe19` for the host-only retry. Both fresh controller builds retained their
original compiler/source proofs; retry performed no builds, learning or generation.
Image IDs:

| Artifact | SHA256 |
| --- | --- |
| Controller learn | `ec1a2c01ed9317b9593c859005fa7d691769a96858b2774c7428aee5650c776b` |
| Controller test | `1e6996bbeaff36b8f58354767cf251b40e6022e5f4e5c180c1acdd28d208716e` |
| Node learn | `71ffe52dd2fbd3e47f80dbc1a7db2f39710a80fdf337a4df7e456b50f8823cd6` |
| Node test | `99809e4f9e76d3606a53dfc575a047580e147a7561188d05603ba86ad7eb1399` |

Published control index:
`9f4f96a33c7774cef24466fcd85031997531a03a8d117f151126baa8d1290dcc`;
AMD64 manifest:
`9b262e9493ab3e0daebc2c9cfbddc6df68b9ac91afc036a759584f3d49d607e5`.
Release manifest verification retained SHA256
`c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74`.

Each complete VM directory was copied and every file SHA256-verified **before**
summary inspection: 15,546 files / 330,622,500 bytes in
`runs/cert-manager-injection-fixes-vm`, then 1,192 files / 46,940,845 bytes in
`runs/cert-manager-injection-fixes-retry1-vm`. Separate full manifests remain in
`.cache/cert-manager/{fixes,fixes-retry1}/result-hashes.json`. Exact runners,
source/image/compiler proofs, original/replacement YAMLs, oracles, API logs,
process outcomes and alarms remain retained. Retry oracle bytes and image IDs
match the source campaign. Original operator evidence/images were preserved.

Reproduction and review commands (VM root `/users/Pial/sieve-ci-20261026`):

```sh
.venv/bin/python .cache/cert-manager/fixes/run-vm.py
.venv/bin/python .cache/cert-manager/fixes-retry1/run-vm.py
# Equivalent individual public replay, with the retained oracle root:
.venv/bin/python -m sieve_tool --repo "$PWD" test -c cert-manager \
  -p GENERATED_YAML -r sieve-cert-manager-fixes-20261028 \
  --output FRESH_OUTPUT --oracle-root RETAINED_ORACLES
# Local independent reconstruction:
.venv/bin/python runs/cert-manager-injection-fixes-audit.py
.venv/bin/python runs/cert-manager-injection-fixes-retry1-audit.py
```

Runners require fresh result paths; retained directories must not be overwritten.
Capacity recovery moved only verified disposable caches to the existing private
RAM volume. Persistent raw evidence/images and the 2 GiB before-cluster floor were
preserved. No local Docker, publishing, push or PR was used.

Coverage remains one Certificate and the seven annotated frames. Shared
CertificateRequest issuer/approver frames stay enabled but unannotated, and the
single-active-worker-per-annotated-frame trace limitation remains. These results
prove the selected replacement schedules, not all candidates, arbitrary concurrent
Certificates, stale-state faults (none generated), or absence of operator bugs.

## Final branch review

The complete branch was reviewed against fork `main` at
`87d1fda877fddc6ed0e08877224e61d08ba67d33` along two independent axes.

**Standards:** corrected generated-plan counts when failed writes are excluded
with pruning disabled, supplied explicit selected-repository working directories
for image loading, and corrected the native informer matrix documentation.
The two documented/correctness findings are resolved. The optional repeated-mode
selection heuristic was retained rather than adding a strategy framework.

**Spec:** closed three reusable detector gaps: reject undeclared spec mutations
in parent patch responses, require explicit current-generation Certificate Ready,
and compare the leaf's complete DNS SAN set with the declared names. Public CLI
negative controls failed before their fixes, covering admission-time changes,
missing observed generation, and missing/extra DNS names. Both reviewers found
no remaining actionable findings after the fixes; operator logic stayed unchanged.

The strengthened checks were also evaluated against all 57 retained baseline/test
outcomes and 36 independent-update sequences, with no failures. This revalidates
existing evidence; it does not count as new VM injections. All original raw
results and alarms remain unchanged.

Final local checks passed 345 Python tests plus 10 subtests, Ruff lint, formatting
of committed files, strict Mypy and workload typing, wheel build, Go race/vet for
all three modules, workflow validation, and Python/reachable-Go vulnerability
audits. The user's pre-existing uncommitted blank line in
`tests/test_trace_errors.py` was preserved and excluded from the PR.
