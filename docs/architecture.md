# Architecture and execution

Sieve has one installed CLI, `sieve`. `python -m sieve_tool` uses the same CLI.
Controller names select profiles under `controllers/`; callers do not pass
source-directory paths as controller identifiers.

## Python package

| Module | Responsibility |
|---|---|
| `cli` | Parse arguments, choose the checkout, and return meaningful exit codes |
| `paths` | Locate repository assets and scope working-directory changes |
| `build` | Fetch sources, install dependencies, instrument, and build images |
| `runtime.cluster` | Create kind clusters, deploy the coordinator and controller |
| `runtime.workload` | Execute the workload and collect logs |
| `runtime.runner` | Orchestrate baselines/tests, postprocess, and save results |
| `analysis` | Build an event graph and select candidate schedules |
| `faults` | Generate intermediate, missed, and stale-state schedules |
| `oracle` | Learn masks/reference states and compare fault runs |
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
   against the learned reference. The cluster is removed after execution,
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

## Batch execution

`sieve test -c NAME -p DIRECTORY --batch` runs that directory's YAML plans
sequentially. Every workload uses a kind cluster named `kind`; run only one
batch at a time per host. Independent VMs can process different directories.
Use external job scheduling for those VMs and copy complete result directories
back for review. The obsolete hardcoded SSH/GNU parallel scripts have been
removed; the CLI does not prune unrelated images or erase earlier results.
