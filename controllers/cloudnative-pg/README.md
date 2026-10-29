# CloudNativePG benchmark profile

The initial calibration targets upstream [#11359](https://github.com/cloudnative-pg/cloudnative-pg/pull/11359).
The unmodified parent `e819964e03fb638c0acaf13b45a9e862c9e3eadb` can recreate a
Pooler's Deployment and Service after deletion starts. The unmodified fixed
revision is `9bb80d2292f5845ac6c2d60dc691b222a5ac4655`. These are a source-level
bug/fix pair, not claims about newly discovered bugs or complete release equivalence.
`benchmark.json` freezes the pair and native build dependencies.

Build with native Go 1.27.1, client-go 0.37.0 and controller-runtime 0.25.0.
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
`default` for Sieve. The upstream v1.30.1 installation YAML is checked against its
frozen SHA256 before server-side apply. Image selection and the Sieve plan are
deployment configuration; operator reconciliation logic is unchanged.

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
