# Architecture and execution

Sieve has one installed CLI, `sieve`. `python -m sieve_tool` uses the same CLI.
Repository selection changes no process working directory.
Controller names select profiles under `controllers/`; callers do not pass
source-directory paths as controller identifiers.

## Python package

| Module | Responsibility |
|---|---|
| `cli` | Parse arguments, choose the checkout, and return meaningful exit codes |
| `revisions` | Verify immutable source checkouts and install tracked integration assets |
| `acceptance` | Run frozen Linux regressions and independently verify raw fault evidence |
| `paths` | Locate repository assets and scope working-directory changes |
| `build` | Fetch sources, install dependencies, instrument, and build images |
| `runtime.cluster` | Create kind clusters, deploy the coordinator and controller |
| `runtime.workload` | Execute the workload and collect logs |
| `runtime.runner` | Orchestrate baselines/tests, postprocess, and save results |
| `events` | Typed observations and resource comparisons, without log I/O |
| `trace` | Decode existing Go log records and retain their source locations |
| `analysis.trace` | Pair operations and assign reconciliation intervals |
| `analysis.event_graph` | Build typed vertices, causality edges and differences |
| `analysis.analyze` | Connect the trace, graph and schedule generators |
| `faults` | Generate intermediate, missed, and stale-state schedules |
| `oracle.artifacts` | Validate existing saved evidence formats with source diagnostics |
| `oracle.learning` | Learn reference values and exclusions from two baselines |
| `oracle.liveness_checker`, `oracle.safety_checker` | Compare explicit in-memory states and histories |
| `oracle.validation` | Check injection/workload evidence and process outcomes |
| `oracle.oracle` | Load selected evidence and save learned reference artifacts |
| `oracle.reporting` | Render existing alarm and perturbation descriptions |
| `results` | Typed verdicts and existing serialized result fields |
| `testing` | Workload driver and convergence helpers |
| `resources` | Packaged harness defaults |

Imports do not run builds or write workflows. The CLI resolves paths before
entering a checkout scope; source-level implementation helpers can still use
repository-relative assets within that scope. The package wheel contains the
Python engine and defaults. Controller profiles, Go sources, infrastructure,
and research fixtures remain checkout assets selected with `--repo`.

## Go modules

`go/client` supplies hooks copied into instrumented dependencies.
`go/instrumenter` rewrites Go source at configured observation points.
`go/coordinator` receives observations, evaluates triggers, and coordinates
fault actions. Existing Go module names and log/RPC formats are retained
because they are used inside instrumented programs. Generated instrumenter
configuration and binaries live in `.cache/`, outside these modules.

## Build, learn, test

1. `sieve build -m learn` or `test` builds an instrumented Kubernetes node image.
   Add `--kubernetes-version` to match the controller's profile. Node sources
   live under `.cache/kubernetes/`.
2. `sieve build -c NAME -m learn` clones the pinned operator into
   `.cache/controllers/NAME/`, copies the client hooks, rewrites dependencies
   and annotated reconcile functions, then builds the image.
3. `sieve learn -c NAME -w WORKLOAD` creates a kind cluster, starts a coordinator,
   deploys the learning image, runs the workload, and records observations.
   It repeats the workload to learn nondeterminism, stores reference data in
   `runs/oracles/NAME/WORKLOAD/`, and writes candidate plans into that run's
   `learn/` directory.
4. Build the test image and run `sieve test -c NAME -p PLAN`. The coordinator
   evaluates plan triggers against hook observations and executes fault actions.
   The oracle compares workload completion, resource history, and final state
   against the learned reference. The owned cluster is removed after execution,
   including setup/workload failures.

Each command uses a fresh `runs/<mode>/<timestamp>-<id>/` directory unless
`--output` selects one explicitly. A per-plan directory is never overwritten.
`--oracle-root` selects another reference tree, including historical
`fixtures/oracles/` when reproducing published bugs. `sieve reproduce` selects
historical plans and oracles automatically; `--skip --output PATH` inspects an
existing reproduction run.

A test command exits unsuccessfully for a harness exception, incomplete
injection, incomplete workload, or raw oracle alarm. Exit status alone does
not establish an operator bug: inspect the recorded injection and workload
logs and classify alarms. `sieve report --results PATH` locates suspicious
saved test results without changing them.

Each newly saved test also writes `fault-proof.json`. It retains all raw alarms
and separates physical injection proof, workload process completion and explicit
application checks. `sieve report --proofs --results PATH` reports these counts
independently and succeeds only when every selected proof verifies the fault and
passes its application checks. Missing application checks are `unknown`; the
finish marker alone does not establish application correctness. The original
report and raw oracle exit behavior remain available.

The initial physical verifier supports one controller restart, requiring an API
Pod deletion, a distinct nonterminating Ready replacement, matching coordinator
action and successful injection process outcome. Independent-update read pauses
use the strict receipt/overwrite/returned-value verifier. Other action sequences,
including stale-state faults, remain explicitly unverified. Application workloads
can write `semantic-outcome.json` with `schema_version: 1` and a nonempty `checks`
list of `{name, passed}` objects. These explicit checks do not mask raw alarms.
Proofs hash their source artifacts; reporting rejects changed source evidence.
`--plans DIRECTORY` additionally counts a supplied generated YAML corpus;
otherwise the generated count is unknown (`null`). Attempts, observed triggered
actions, physical verification and completed workloads are reported separately.
Counting a generated corpus does not imply that every candidate was executed.

## Batch execution

`sieve test -c NAME -p DIRECTORY --batch` runs that directory's YAML plans
sequentially. Every workload uses a kind cluster named `kind`; run only one
batch at a time per host. Independent VMs can process different directories.
Use external job scheduling for those VMs and copy complete result directories
back for review. The obsolete hardcoded SSH/GNU parallel scripts have been
removed; the CLI does not prune unrelated images or erase earlier results.

Before replay, `sieve check-plans --plans DIRECTORY --trace LEARN_LOG` reports
the generated plan count, stale-state schedule count and hashes without editing
the YAMLs. It checks successful-write occurrence predicates against the baseline
and warns when all observed producers of a resume resource use the paused
consumer. Validated independent workload updates are recognized as another
producer. Other predicate types remain explicitly marked for review. These are
baseline diagnostics, not a proof that a schedule is reachable, impossible or
physically effective; dynamic reachability still requires execution evidence.

## Host dependencies and experiment dependencies

The host package uses Python 3.14 and current Docker/Kubernetes SDKs. Go 1.27.3
builds the coordinator and AST instrumenter. They are built with `GOWORK=off`
so each module uses its declared graph.

`go/client` is copied into operators and Kubernetes API servers. It uses only
the Go standard library: object metadata is read through interfaces and narrow
reflection adapters, and configuration arrives as JSON in the existing
`sieveTestPlan` ConfigMap entry. Human-authored plans and the coordinator's
`server.yaml` remain YAML; Python converts the payload when creating the
ConfigMap. Typed and unstructured Kubernetes objects are covered by a contract
test using the current SDK.

Adding the injected module no longer replaces `apimachinery` according to the
cluster version. The operator's own dependency graph controls that choice.
Pinned operator commits, compiler images, and Kubernetes releases describe
the experiment being tested; they are independent of the host SDK versions.
Existing profiles are historical targets, not a claim of support for current
Kubernetes API-server/informer internals. Porting those hooks to a new target
requires separate source and fault validation.

For Kubernetes sources with a native Go workspace, node builds register the
injected client as a workspace module and add its explicit vendor metadata.
Older sources keep the existing vendored-client link. The instrumenter and
coordinator still build with `GOWORK=off` independently of the test subject.

DeepDiff comparisons explicitly use field-level dictionary changes and
positional list comparisons. Dependency upgrades must keep the oracle's
ability to detect missing operands, ownership changes, and other real failures.
CI audits the installed lockfile environment and reachable Go packages, and
checks that the injected client remains free of third-party modules.

External execution is centralized in `process`, with finite deadlines and owned
process sessions. Builds track Docker helper containers separately. Workloads
record process outcomes alongside raw alarms, and failed baselines cannot create
oracles. See [process execution](process-execution.md) for defaults and overrides.

## Trace interpretation

The Go hooks and coordinator still emit the same tab-delimited records. Python
reads those records through `sieve_tool.trace`; analysis and oracle consumers
share that decoder. Oracle readers select only API-server records, so unrelated
controller records do not affect their input. A malformed selected record raises
`TraceError` with the source file, line, marker and available operation ID. Raw
logs are never rewritten. Kubernetes object bodies remain schema-independent
maps; the surrounding event fields have concrete types.

The decoder also recognizes Kubernetes' quoted klog text envelope. It unwraps
the message once, including Go ASCII hex escapes, before decoding the existing
tab-delimited record. Payload strings, marker selection and physical source
positions are retained; malformed quoted messages remain located input errors.

A record's position is its zero-based physical line number, including ordinary
log lines. Analysis uses that position for ordering and interval endpoints. The
existing `start_timestamp` and `end_timestamp` names do not represent wall-clock
time. Receipt/write IDs identify pairs; their numeric order is irrelevant.
Reconciliation IDs remain opaque strings, including leading zeros.

Full-object and `PartialObjectMetadata` informer caches can watch the same
resource independently. Metadata receipts append `metadata-informer` to the
before-hear record; omitted provenance retains the existing `informer` observer.
Differences, overwrite detection and occurrence counts are computed separately
for each resource/observer stream. Generated controller-receipt triggers use
that observer through the existing `observationPoint.by` field. Replay keeps
object states and trigger counts separate as well. Resource keys, API-server
observations, read-pause scope and fault actions are unchanged. Multiple caches
of the same representation still share an observer; this does not provide
arbitrary per-informer identity.

`analysis.trace.assemble_trace` creates an independent projection, leaving decoded
records unchanged. It preserves these existing rules:

- An unfinished receipt ends at the trace's line count. An unfinished write or
  annotated call produces no completed write. A reconciliation can remain open
  at EOF; interrupted runs are not automatically malformed.
- Legacy records permit one active worker per reconciler function. Different reconcilers can
  interleave. Read keys/types are shared across those active reconcilers and
  cleared when no reconciliation remains active.
- Trivial-reconciliation compression and relaxed boundaries follow the profile's
  existing switches. They do not reorder observations or change pruning rules.
- Duplicate/unmatched receipt and write pairs and overlapping workers are harness
  input errors. Null Get results and null/empty List results remain valid reads.
  Graph differences still require object payloads; a null payload at that stage
  is an unsupported input, not an operator alarm.

New learning hooks append a `SIEVE-V2` identity to reconciliation boundaries and
their reads/writes. A process-scoped invocation token distinguishes overlapping
workers; a receiver type/address identifies controller instances within that
process. Receiver addresses are observational identities, not replay selectors.
The coordinator acknowledges each boundary and preserves the token on subsequent
observations. Analysis keeps read dependencies per invocation and retains every
explicit invocation rather than compressing distinct workers. Legacy trace and
plan contracts keep their original interpretation. Unknown invocation tokens and
mismatched instance endings are located input errors.

An instance retains its previous interval checkpoint across completed
invocations. Sequential nontrivial calls retain the legacy lower bounds; trivial
compression can retain a checkpoint without changing real invocation identities.
Overlapping calls share a conservative checkpoint until that instance is
quiescent, with read dependencies still isolated per invocation.

Cache-store interfaces have no context argument. The observer therefore obtains
the current goroutine identity from the runtime stack header and maintains a
stack for nested annotated calls. Unsupported headers produce an observer error
and prevent learning. Identity errors and client RPC errors in either observer
log reject the baseline before oracle construction or plan generation. Work
spawned in another goroutine needs its own annotated
entry; scope is not propagated across arbitrary asynchronous work. Instrumentation
does not change worker counts, watches, scheduling, or operator state. Public RPC,
overlapping-hook, 32-worker cold-start race and compiled-instrumentation tests
cover identity propagation and unchanged function return behavior.

Two existing limitations are deliberately preserved. The decoded `from_cache`
flag remains false for both cache and REST records because the previous decoder
compared a boolean with a string; `TraceRecord.marker` retains the actual source.
Annotated writes inside reconciliation remain unsupported because their previous
implementation called a missing range-setting method. They now report a located
error. Adding range semantics or changing read provenance in analysis requires a
separate behavior change and validation.

`fixtures/traces/` freezes decoded fields, intervals, graph contents, derived
values, pruning counts and full plan contents from the engine before this
refactor. It includes real Secret Generator and RabbitMQ traces plus interleaved
and interrupted synthetic histories. See its README for sanitization and capture
provenance. These expectations are reviewed evidence, not generated test output.

## Oracle boundaries

State/history comparisons and baseline learning accept in-memory values. They
perform no artifact reads and leave the supplied evidence unchanged. Comparison
policy explicitly carries the workload and existing common/controller settings.
Controller-family exclusions are supplied once per comparison, including the
reference and current families for end-state checks. Existing masking, random-name
counts, positional list handling, alarm wording and category ordering are retained.

`oracle.artifacts` reads the existing JSON formats for state, history, event counts,
field masks, controller families and process outcomes. Kubernetes object bodies
remain schema-independent. Invalid JSON, duplicate object keys, non-JSON numeric
constants and incorrect artifact field types raise `ArtifactError` with the file
and field location. Validation messages omit input values. Artifact errors remain
harness exceptions and cannot become operator alarms or successful test results.
Only artifacts selected by enabled checks are loaded; missing process outcomes
retain the existing marker-based validation used by older evidence.

`oracle.oracle` handles file loading and saving. It passes data to the pure checks
and returns `results.TestResult`. The result model retains separate common,
end-state and history alarms, injection/workload completion and harness exception
fields. Its serialization preserves the existing result JSON fields and alarm
ordering. A completed coordinator action sequence still requires actual matching
read holds/releases for read-pause schedules. Reporting has its own module and
does not make verdict decisions.

The 38 frozen verdicts in `fixtures/oracle-contracts/` include retained clean and
incomplete Secret Generator runs plus explicit operand failures. Reference data,
learned masks, exact alarms and serialized result records are compared against
the preceding implementation. These snapshots retain existing interpretation,
including the learner's handling of type changes; changing verdict rules requires
separate review and evidence.
