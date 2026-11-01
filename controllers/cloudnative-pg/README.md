# CloudNativePG benchmark profile

## Current default subject

The default profile now pins `96cb6b05aea5f76ddac43d21e0f178e0a8b1b331`, the
CA Secret fix verified by the [bounded affected/fixed replay](../../docs/rfe/managed-child-repair/cloudnativepg-ca-cache-replay-validation.md).
Builds use that revision's native Go 1.26.4, client-go 0.36.2,
controller-runtime 0.24.1 and unchanged upstream Dockerfile. Installation uses
`releases/cnpg-1.30.0-rc1.yaml` from the same exact commit, checked against SHA256
`ff1aca776fccf6ae921c8e12648e6c415fb9a411530da3213eb89592aeaa10a9`.
This includes all eleven native CRDs and the `DatabaseRole` watch permissions.
The operator retains ten reconciliation workers. Build its `vanilla` image
before learning or testing so instance managers use that same fixed subject.

Pinned commits that are absent from the normal clone are fetched explicitly
from the configured upstream before checkout. A failed fetch stops the build.
The original Pooler calibration pair in `benchmark.json`, previous runtime
snapshots, images, learned references and reports remain historical evidence.
Future experiments must record their selected source and matching reference;
changing the default does not transfer earlier plan coverage to this revision.

## Failover quorum configuration experiment

`failover-quorum-update` uses three dedicated Kind workers, one PostgreSQL
instance per worker, and keeps clients on the control plane. Operator concurrency
remains ten. The public workload CLI and its external Kubernetes/Docker command
boundary are the test seams; tests do not mock native or private reconcilers.

The workload enables quorum checking after bootstrap to initialize its lazy
informer through normal reconciliation. It verifies required `ANY 2` replication
and starts the existing numbered-write/read client. It disconnects worker A,
changes to required `ANY 1`, verifies effective SQL and authoritative quorum
metadata, commits an explicit `remote_apply` witness and verifies it on B.
It then disconnects the primary and B before reconnecting A at its original
network address. A must lack the witness. During a 120-second observation,
automatic failover must be denied; lack of write availability is expected.

The fault plan delays only this Cluster's FailoverQuorum API-cache delivery
after the accepted reduction, retaining previously delivered `W=2` metadata.
It releases on the workload's recorded end-of-observation phase. Actual hold,
release, stale successful read, current candidate set, lease and SQL promotion
must be independently verified. No quorum status is edited or promotion forced.
The plan also records the object creation and prior informer delivery needed to
establish that the native cache was initialized before the delay.

All disconnected workers are reconnected in a finally block, including ambiguous
command failures, before recovery and final acknowledged-data checks. The primary
and B isolation stage is capped at 210 seconds plus bounded restoration. Every
client attempt, accepted mutation, SQL receipt, network inspection and restoration
is retained. An unsafe promotion or lost witness remains a failure after recovery.
Two fresh learning baselines and an independently verified matched test-image
control gate the first fault. A second matched control/fault pair is reserved for
confirming a meaningful failure. The campaign permits at most two faults, six
workloads, 900 seconds per workload and 1200 seconds per single-run service, with
zero automatic repeats. Images are reused only after recorded identity checks.

## CA Secret bootstrap regression experiment

`ca-secret-bootstrap` creates a three-instance Cluster with the default shared
server/client CA, preserves sampled bootstrap errors even after recovery, and
requires owned PKI Secrets, three Ready database Pods, all 32 acknowledged SQL
rows on each instance, one writable primary, unchanged Cluster UID/spec/generation,
and stable readiness during the observation window. A recovered bootstrap error
is a semantic failure; it is not evidence of an indefinitely stuck cluster.

The manually authored `plans/ca-secret-bootstrap/delay-ca-receipt.yaml` holds the
leading API server's first CA Secret cache delivery. It releases after both a
duplicate CA request and an observed Cluster auxiliary-object error, or after the
controller reaches replication-certificate creation. There is no timer release,
fabricated cache result or disabled watch.
An independent physical review must establish actual hold/release, persistence
of the first CA Secret, a duplicate request/rejection on the affected source,
and the absence of that duplicate on the fixed source. Action completion alone
does not prove the documented race occurred.

The historical experiment uses separately sealed runtime configuration for
upstream [#10989](https://github.com/cloudnative-pg/cloudnative-pg/pull/10989):
affected `105265f5b702b7dcfaa41f7d8e1a45123f1c8ee1` and fixed
`96cb6b05aea5f76ddac43d21e0f178e0a8b1b331`. These differ only in that fix and
its test. Their native dependency versions and historical installation manifest
are retained. The fixed member is now this profile's default subject. Two
fixed learning baselines and an affected no-fault control gate two affected
fault attempts and one fixed fault attempt: six workloads maximum, 900 seconds
per phase, zero automatic repeats. All oracle verdicts and earlier evidence
remain preserved. This is known-bug calibration, not a new bug claim.

## Pooler identity replacement experiment

`pooler-replacement` is separate from the original calibration workload. It
creates a healthy one-instance Pooler, records its children and UID, and adds
phase B. Ten seconds later it deletes that UID with Background propagation and
creates a Pooler under the same name with a new UID and two instances. A
UID-tested phase-C patch supplies an independent release condition. It checks
convergence and stability of correctly owned replacement children, the requested
replica count, unchanged Cluster identity/spec, all 32 acknowledged database
rows on three instances, and exactly one writable primary.

The two manually authored plans in `plans/pooler-replacement/` use existing
Sieve actions: hold a Pooler read after it fetched the old object until the new
identity's C receipt, or restart after API delivery of C. These plans are not
generated-plan coverage. The after-read hold requires a separate physical audit
of actual hold/release, the returned old UID and replacement API/cache ordering;
the generic missed-state proof does not establish that scenario. No pause is
released by a timer. Native operator code, worker counts and prior evidence are
unchanged. A passing no-fault control and two healthy learning baselines gate
fault execution. The experiment caps ordinary fault attempts at two, plus at
most two failure-confirmation runs.

## Continuous clients during replacement

`pooler-replacement-clients` uses the same replacement sequence and old-read
pause actions. Before phase B, an independent Pod starts two fresh-connection
query loops: one through `sieve-pooler.default.svc`, the other directly through
`sieve-db-rw.default.svc`. Both must produce successful warm-up queries. Every
attempt records timestamps, stdout, stderr, exit code and timeout; the expected
aggregate is `32|528|32`. The benchmark creates its own public synthetic SQL
login and reads no existing authentication Secret. The client Pod has no owner
reference, Cluster label or service-account token mount.

Each loop starts a query every 0.2 seconds after its previous result, caps each
query at four seconds, and stops after 420 seconds plus at most one in-flight
query. It normally stops after the replacement has converged and the ordinary
30-second observation window completes. Complete contiguous logs, clean client
termination, correct results and successful post-convergence queries are required.
Connection errors during the deliberately destructive replacement are measured,
not automatically called an operator defect. Compare them against the matched
`actions: null` control on the same test image. This measures fresh connection
availability; it does not establish persistent-session or transaction behavior.
The finite experiment allows one vanilla setup control, two learning baselines,
and at most two matched no-fault/paused-read pairs, with 900-second phase limits.

## Readiness-probe timing isolation

`pooler-replacement-fast-probe` repeats the continuous-client replacement experiment
with a declarative Pooler Pod-template override: the same TCP readiness check on
port 5432, timeout five seconds, success threshold one and failure threshold three,
with its period reduced from the native default ten seconds to one second. Both
old and replacement Poolers use this template, in matched no-fault and stale-read
runs. The operator's existing template override API is used; no controller, API or
operand image is changed or rebuilt. The profile fails before phase B/deletion if
the effective initial Deployment does not have the requested probe and checks all
observed replacement Deployment probes as well. Independent VM review also checks
the actual Pooler Pod specs. Earlier profiles, plans and evidence remain separate.

This reduces probe-phase timing uncertainty to a one-second cadence; it does not
assume every difference in Pod startup or DNS resolution disappears. Actual client
recovery and API/Service/Pod timelines still determine the result. Two fresh learning
baselines gate at most two matched test-image pairs, each with the same original
pause/release action definitions, ten operator workers, bounded query loops and
900-second phase limits. No automatic retries or oracle/mask changes are allowed.

## Original calibration

The initial calibration targets upstream [#11359](https://github.com/cloudnative-pg/cloudnative-pg/pull/11359).
The unmodified parent `e819964e03fb638c0acaf13b45a9e862c9e3eadb` can recreate a
Pooler's Deployment and Service after deletion starts. The unmodified fixed
revision is `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`. These are a source-level
bug/fix pair, not claims about newly discovered bugs or complete release equivalence.
`benchmark.json` freezes the pair and native build dependencies.

The original calibration used native Go 1.27.1, client-go 0.37.0 and
controller-runtime 0.25.0; these are preserved with its sealed runtime snapshots.
The original upstream Dockerfile, pinned base images and licenses are retained.
The benchmark runner supplies `SIEVE_SUBJECT_COMMIT` for the source provenance
banner. Learning adds observation calls to the Cluster and Pooler reconciliations;
test mode leaves original operator Go files unchanged. All controller registrations
and the release manifest's default ten reconciliation workers remain enabled.
Other reconciler frames and instance-manager internals are outside this initial
trace scope. Instance managers use the same upstream revision's uninstrumented
`vanilla` image in every mode. Build that image before learning or testing. This
keeps operand binaries constant across the fixed control, learning and test runs
without masking image fields in the oracle. The main controller alone uses the
selected instrumented mode; all instance-manager API events remain API-captured.

The main controller, service account, bindings and webhook routing are adapted to
`default` for Sieve. The original calibration checked the upstream v1.30.1
installation YAML against its frozen SHA256 before server-side apply. The current
default uses the commit-matched installation described above. Image selection and
the Sieve plan are deployment configuration; operator reconciliation logic is unchanged.

`pooler-finalizer-updates` creates three PostgreSQL instances and verifies 32
acknowledged SQL rows. It first deletes a Deployment under an active Pooler and
requires a distinct Ready replacement. It then supplies UID-tested A/B/C/D
metadata updates eight seconds apart. B installs a benchmark-owned finalizer;
the workload requests owner deletion and independently deletes the owned
Deployment and Service before C. D and a 45-second observation window follow.
It checks for any recreated children while the owner is terminating, preserved
Cluster/Pooler identity/specification, matching SQL data on all three instances,
and exactly one writable primary. Kubernetes may advance a custom resource's
generation when accepting deletion. The exact returned deletion object declares
the expected generation for C/D and final checks; UID/spec stay unchanged, and
any further undeclared generation change still fails. The accepted deletion
object is saved as `declared_owner_after_delete`. It retains all accepted mutations
and returned
objects in `workload-updates.json` and `benchmark-observations.json`, and writes
explicit checks to `semantic-outcome.json`. Application failure returns nonzero.
Accepted deletion receipts are saved before repair waits, including when the
workload is interrupted before convergence.
Setup waits within the existing readiness timeout for both initial children to
exist before checking their owner UIDs. A Ready Deployment can precede Service
creation when a restart interrupts the creating reconciliation. Missing children
still time out, and an existing child with a different owner still fails.
Readiness lookups and polling sleeps share that timeout; a stalled lookup or
an oversized polling interval cannot extend it or start mutations afterward.
An injected controller restart can temporarily remove the Pooler admission
webhook. If the API explicitly rejects a request because that webhook's connection
is refused, the workload retains the failed command logs and retries the same
request within the existing readiness timeout. It records each retry in
`benchmark-observations.json`; policy denials, ambiguous failures and exhausted
timeouts still fail. This adds no reconciliation wait between accepted updates.
Independent Deployment and Service watches start before the A/B/C/D sequence and
must acknowledge the original children before deletion begins. Their complete
event streams must acknowledge both deleted child UIDs and remain active through
the observation window and SQL checks, so a
short-lived recreated child is counted even when ordinary Gets miss it. Broken
or incomplete watch streams are harness failures, not healthy cleanup evidence.
The raw `owned-child-watch-*.json-stream` files contain kubectl's structured watch
events; incremental JSON decoding avoids its text printer's tab expansion.

The benchmark pins PostgreSQL and PgBouncer by digest using
`SIEVE_CNPG_POSTGRES_IMAGE` and `SIEVE_CNPG_PGBOUNCER_IMAGE`.
PostgreSQL references retain their version tag before the digest, for example
`postgresql:18.6-system-trixie@sha256:…`; native admission needs that tag for
upgrade detection. Bare digest references are rejected before Kubernetes setup.
Published operands are pulled through the node's CRI and must resolve by config
ID as kubelet expects. `deployment/node-operand-images.json` records those IDs;
the bounded pull/lookup logs are retained with the deployment evidence. This
avoids a successful archive import leaving a dangling synthetic image reference.
Public workload CLI
options can select these images and finite diagnostic timing; the VM benchmark
uses the documented full timing, not the accelerated boundary-test values.

Learn two fresh baselines from the fixed subject. Select the public generator's
B-to-C Pooler read-pause plan and replay its YAML unchanged on both subjects.
A verified miss requires real held/released reads, every observed cache stream's
accepted overwrite and a returned later accepted UID/resourceVersion/state.
Keep uninstrumented controls and repeated attempts separate from unique coverage.
Final review must independently reconstruct API deletions/recreations and database
checks, preserve every raw alarm and verify complete local/VM evidence hashes.

`rolling-config-update` uses three instances and required quorum synchronous
replication (any one standby), with effective SQL checks before writes. Independent
in-cluster read and numbered-write streams use fresh rw-service connections every
200 ms, a five-second command deadline, and remote_apply for every write. Only a
successful post-COMMIT receipt acknowledges an ID; all other write outcomes remain
ambiguous. Full attempts and terminal records are retained.

After ten seconds of healthy client traffic, max_connections increases from 100
to 120, then to 140 four seconds later without waiting for the first rollout.
The pinned API defaults primaryUpdateMethod to restart. Replicas roll out by Pod
replacement, while a configuration-only primary restart can happen in-place.
These plans use Cluster events and do not require Pod deletion. Final SQL verifies 140, no pending
restart, changed postmaster start time on every instance, roles and synchronous
replication, and every acknowledged payload on all instances after clients stop.
A thirty-second observation follows convergence. Disconnections are retained for
comparison with matched controls and do not by themselves fail an invariant.

The rolling plans delay ClusterReconciler after an actual cache Get until the
latest input reaches the informer, or restart the controller after the latest
update is accepted. Prove the returned older value and actual hold/release, or
the old controller deletion and distinct Ready replacement, from raw events.
Generic missed-state proof does not support after-read pauses; retain that verdict
and use a separate physical audit. Use fresh baselines and a test-image no-fault
control before faults, with at most four fault attempts including confirmations.
