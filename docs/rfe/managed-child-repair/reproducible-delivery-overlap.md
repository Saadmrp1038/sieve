# Reproducible delivery overlap

A test plan can opt into one bounded timing fault through `deliveryOverlap`.
The scheduler holds one identified reconciliation's successful full cache Get
reply. The workload waits for an explicit held-input receipt before submitting
the next input. Only the completed full informer receipt for the same resource
UID, next generation, distinct resourceVersion and configured next phase releases
the call. The matching invocation end completes the schedule.

```yaml
workload: metadata-labels-overlap
actions: []
coverageResources:
  - externalsecret/default/sieve-metadata-subject
deliveryOverlap:
  mode: hold
  resourceKey: externalsecret/default/sieve-metadata-subject
  reconciler: github.com/external-secrets/external-secrets/pkg/controllers/externalsecret.(*Reconciler).Reconcile
  holdPhase: metadata-2-change
  releasePhase: metadata-3-remove
  timeoutSeconds: 20
```

The only modes are `observe` and `hold`. The resource must be an exact covered
scope, the reconciler an exact name (including a literal Go pointer receiver),
and the two phases distinct. Phases use the `sieve.testing/state` label. A new
schedule requires explicit `actions: []`; combining other actions is rejected.
The hold deadline must be an integer from 1 to 60 seconds. Existing plans omit
this option and retain their behavior.

The selected read is bound to its invocation, receiver instance, UID, generation
and actual read version. Metadata-only reads cannot arm the hold. Partial or
unrelated informer receipts cannot release it. One observer serializes records,
but it does not retain its record lock while a worker waits. Other invocations,
receivers and resources remain available; worker configuration is unchanged.
A deadline, cancellation or observer error invalidates the attempt and releases
the held call. There is no command that manually reports a successful release.

The coordinator exposes status and bounded wait queries through its existing
RPC interface. The public command, run inside the owned Kind node, emits JSON:

```sh
/sieve/coordinator/sieve-coordinator overlap status --timeout-seconds 25
/sieve/coordinator/sieve-coordinator overlap held --timeout-seconds 25
/sieve/coordinator/sieve-coordinator overlap completed --timeout-seconds 25
```

For declared test plans, the runner passes `--delivery-overlap` to the metadata
workload. It queries the configured mode before setup. Hold mode waits after the
acknowledged change, validates the selected UID/generation and invocation, submits
removal, then requires matching completion and release version before semantic
convergence. Observe mode runs the same history without holding or waiting for a
hold. Raw query output, input acknowledgements, failures and semantic samples
remain in the command journal and metadata observations.

Fault proof requires the ordered begin/Get/full receipt/end witness plus one
matching held/released/completed lifecycle and workload receipts. It binds the
hold to the actual read version and the release to the exact acknowledged newer
input. Process completion alone cannot prove the fault. Observe mode explicitly
reports that it contains no injected timing fault. Semantic outcome and raw oracle
alarms stay separate from physical proof.

Public plan, client/coordinator RPC, compiled coordinator command and workload
CLI contracts cover the protocol. Tests include ten concurrent workers, normal
release, wrong UID/partial receipts, metadata-only reads, cancellation, deadline,
malformed workload receipts and missing or inconsistent physical evidence. Local
fixtures do not establish native operator behavior. Native validation requires
rebuilt test-mode operator images, verified matched controls and repeated physical
witnesses; the campaign report records what actually ran.

The initial bounded validation envelope is one fresh matched observe control and
two fresh hold executions per operator, External Secrets and cert-manager: six
workloads, at most four timing faults, ten-worker settings, no automatic retries.
The selected cert-manager frame is its issuing controller, which was observed
reading the change input in the preceding verified learning evidence. Each hold
counts as a fault even though the ordinary action list is empty. Images, inputs,
raw results and failed attempts must remain retained. Full VM result trees must
be copied and hash-verified before inspection; while a stage runs, only its
completion marker and service state may be checked.
