# Bounded operator sequences: verified restart family across two operators

The first slice is complete within the approved **nine-workload, two-fault**
bound. External Secrets and cert-manager each passed a physically verified
restart during the selected a→b→c→a history. Both recovered with correct latest
state, ownership and more than fifteen seconds of sampled stability on the new
Ready Pod. Both fault runs and both eligible matched controls had zero raw
alarms. **No operator defect was found or claimed.**

The nine attempts comprise four learning baselines, two successful matched
test-image controls, two faults and the retained failed External Secrets control
from the initial phase. Eight workloads passed. The earlier failure was the
host artifact collision fixed with public-CLI TDD in `9a7c8be`, before the
replacement control and faults. It remains counted. The user explicitly chose
nine workloads/two faults after the earlier eight-workload estimate was
superseded; no additional allowance or automatic retry was inferred.

| Continuation execution | Physical outcome | Semantic outcome | Final sampled stability | Raw alarms |
| --- | --- | --- | --- | --- |
| External Secrets repaired matched control | One Pod, no restart | Passed | 15.504 s | 0 |
| External Secrets restart | Old Pod deleted; distinct Ready replacement | Passed | 15.031 s | 0 |
| cert-manager restart | Old Pod deleted; distinct Ready replacement | Passed | 15.611 s | 0 |

The repaired External Secrets control reused its two independently verified
learning references. The host fix changed local artifact handling, regression
tests and documentation; native images, input sequence, monitors, adapters,
worker flags and frozen plans were unchanged. The new matched control ran the
corrected host harness and exact test image before its fault. cert-manager used
its already verified corrected-host matched control. Both learning references,
control summaries, manifests, source delta and review hashes are bound in the
continuation protocol/jobs. Neither failed control nor coordinator completion
alone was accepted as a passing control.

Each fault was the same generated family: one asynchronous `restartController`
after the full informer received the first labeled update, `sequence-1-b`.
The receipt's parent UID, generation 2, spec, phase and resourceVersion matched
the accepted API response in both runs. Raw API evidence showed old controller
Pod deletion, a new nonterminating Ready Pod and a valid controller-owner chain.
The existing Sieve mechanism deletes the owning Deployment and recreates its
spec. Accordingly, old/new Deployment and ReplicaSet UIDs are distinct; the
Deployment name, namespace, labels and full spec are unchanged. Requiring the
same owner UIDs would contradict that mechanism. The continuation review follows
exact owner references and checks the target Pod-template label rather than
assuming a label on Deployment metadata.

Both old and replacement Pods ran the exact verified imported image bytes and
ten-worker argument, with zero container restarts. CRI image IDs were matched
to the content-addressed single-image export indexes and their verified OCI
configuration/layers/binaries. Recreated Deployment specs preserve the worker
flags and image. API logs recorded replacement Ready before the final two
accepted updates in these runs. The final stability intervals use the new Pod
UID; the greatest final-interval sample gap was 0.871 seconds. Samples recorded
six `controller_recovery` pending observations per fault, plus two/five pending
latest-state observations for External Secrets/cert-manager. These were expected
recovery/reconciliation intervals, not Ready-with-wrong-data violations.

Independent application review rechecked accepted updates against API events,
parent identity/generation/spec, exact child ownership and actual Fake-provider
data or TLS/SAN/key material. Physical effect, application outcome and raw
alarms were evaluated separately. The successful results are recovery evidence
for this bounded scenario; they do not establish absence of operator bugs.

The physical-review CLI additionally rejected **fourteen altered-copy probes per
operator**: missing Pod deletion or replacement Ready, wrong deletion UID,
namespace or scope, wrong workers/image name/imported digest/owner, changed
Deployment spec, missing Deployment deletion, wrong phase or receipt UID and
incomplete coordinator completion. Each copy passed before alteration, then
failed after alteration. All original evidence remained hash-identical. These
28 probes validate rejection behavior; they are not real faults or discovered
operator defects. The initial owner-selection reviewer failure and its correction
are retained alongside all prior harness/reviewer failures.

Product source remains the TDD implementation validated by **492 tests plus ten
subtests**, strict typing, lint/format and wheel build. This continuation changed
experiment drivers/reviews and reports, not product or operator reconciliation
code. The source remains at `9a7c8be`; native image/source verification from the
initial report still binds the pins: External Secrets
`fc0dd82da6422c6ec840d24425bbda68e5259aab` (Go1.27.3, client-go0.37.1) and
cert-manager `925c22283de556172d48d96a165dc2fa27f7a6ff` (Go1.26.10,
client-go0.36.2). Ten workers per controller were preserved. There is one parent
key and one selected history per operator; fourteen generated candidates do not
mean fourteen executed histories. Restart occurs at the first update's receipt,
not at arbitrary internal instructions or every possible interleaving. Sampled
stability can miss failures between samples. Fake/SelfSigned providers do not
cover production-provider behavior or unrelated CRD semantics. No generative
evaluation or adaptive campaign was added in this slice.

Only service state and DONE were read while each stage ran. At termination,
every result tree was copied in full and every file matched VM SHA256 values
before inspection. Final audit reverified all **seventy raw result trees**:
310,173 files and 16,429,255,727 bytes, including the sixty older trees, seven
initial-slice trees and three continuation trees. All 254 sealed initial-slice
inputs/reports remain hash-identical. The protected test remains unstaged at
SHA256 `b41c28229b13ec00e5f9eef524943f5348463a10618a9db0c57ead40d1491287`.
The VM is idle with no containers or Kind network attachments; all 126 preexisting
named image references retain their IDs. No credential inspection/copying,
generative evaluation, local Docker, image pruning, push or PR occurred.

The [initial bounded report](operator-sequences-first-slice-bounded-validation.md)
remains unchanged as the historical six-workload checkpoint. The complete
machine-readable outcome is
[operator-sequences-first-slice-fault-results.json](operator-sequences-first-slice-fault-results.json).
Raw continuation evidence, complete manifests, independent verdicts and retained
probe copies are under `.cache/operator-sequences/first-slice-faults-20261101/`.
Both workload and fault budgets are exhausted; no further execution is authorized.
