# Native delivery-overlap validation checkpoint

The shared protocol is implemented for External Secrets and cert-manager in
`00bd12209f9257f3a00018f887a19836f5b541bb`. Native validation is incomplete:
External Secrets passed its matched control and reproduced the scheduled overlap
twice; cert-manager stopped during the image build.

| Operator | Rebuilt test image verified | Matched controls | Verified overlap executions | Semantic passes |
| --- | --- | --- | --- | --- |
| External Secrets | Yes | 1 | 2 | 3 |
| cert-manager | No | 0 | 0 | 0 |

External Secrets remains pinned to `fc0dd82da6422c6ec840d24425bbda68e5259aab`.
Independent review accounted for all 812 original Go files and 368 native module
versions. Only the approved observation wrappers/imports changed native Go code.
The packaged image binary matches the native build artifact. Its matched control
and both holds used the same image, history, inputs, monitors and ten-worker flag;
control versus hold plans differ only in scheduling mode. The two hold plans are
byte-identical and ran with distinct subject, reference and controller UIDs.

Both executions bind a generation-3 cache Get to its native invocation and receiver,
then show held state, completed full generation-4 informer delivery, release,
invocation end and completion in that order. Each held read version matches its
actual Get; each release version matches the acknowledged removal input. All four
subject inputs were read and all three full updates were delivered in every run.
Cleanup and fresh-history equivalence passed sampled stability windows exceeding
15 seconds. No differential oracle alarms, observer errors, controller restarts or
controller deletions occurred. Startup infrastructure warnings and the intentional
Fake provider warning remain in the raw evidence and have separate adjudications.

Cert-manager remains pinned to `925c22283de556172d48d96a165dc2fa27f7a6ff` and native
Go 1.26.10. The harness inherited that compiler selection when building Sieve's
instrumenter, whose module requires Go 1.27. The failed build is retained. An
isolated build-tool routing fix, rebuilt cert-manager image, matched control and
two overlap executions remain outstanding. The proposed new regression-test seam
is the public image-build CLI with external Git/Go/Docker responses; confirmation
is pending. No compiler pin was changed and no cert-manager workload launched.

The campaign has executed three of at most six workloads and two of at most four
timing fault attempts, with no automatic retries. All three entire result trees
were copied and compared file-for-file against VM SHA256 manifests before review.
The complete first build tree and idle VM input directory were also retained and
verified. The independently verified older test node image and learning references
were reused with explicit provenance. The VM is idle and all 125 preexisting image
references retain their original IDs. All 75 older raw result trees and all five
predecessor evidence seals were verified unchanged. The protected test retains
SHA256 `b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287` and was
excluded from execution, formatting and staging.

Local validation passed 609 Python tests plus 10 subtests, race tests and vet in
all three Go modules, strict checks for 45 shared Python sources, lint/format and
the wheel build. Public fixtures cover both operators; they do not substitute for
cert-manager native validation. No previously unknown operator defect is established.
This checkpoint demonstrates repeated native overlap for one label history on
External Secrets, not arbitrary histories, continuous correctness or same-key
parallel reconciliation.

Raw evidence, frozen inputs, TDD logs, manifests and independent reviews are in
`.cache/operator-sequences/reproducible-overlap-20261102/`. The machine-readable
checkpoint is `external-secrets-phase-review.json`; `CONTINUATION.md` records the
pending work and exact observation/collection commands. Nothing was pushed and
no PR was opened.
