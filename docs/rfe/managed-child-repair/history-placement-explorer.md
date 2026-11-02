# Bounded metadata history and placement exploration

The explorer adds remove/re-add and restore-value histories for External Secrets
and cert-manager. Each history has three operations, runs against either labels
or annotations, and can hold the read after step 1 until step 2 is delivered, or
after step 2 until step 3 is delivered. Across both operators this is sixteen
cases. All cases retain ten reconciliation workers.

| History | Initial state and three accepted changes |
| --- | --- |
| remove/re-add | absent → one → absent → two |
| restore value | absent → one → two → one |

The existing eight-history catalog, default workload names and schema-1 evidence
remain supported. New workload names encode history and placement, for example
`metadata-labels-remove-readd-at-2-overlap`. This keeps Sieve references and result
paths distinct. Go instrumentation, native compiler pins and deployment manifests
are unchanged. Controller profile `num_workers: 0` means zero Kind worker nodes;
the operator's reconciliation-worker argument remains ten.

Generate a fresh bounded set from the repository root:

```sh
uv run python -m sieve_tool.testing.metadata_explorer plan \
  --max-cases 8 --output .cache/metadata-exploration-demo
```

Both operators are selected by default. Repeat `--operator` to select them
explicitly, or select just one. The strict case cap is 1–16. Selection is
deterministic: the later adjacent edge comes first, with operators interleaved.
A cap of eight selects both histories and metadata maps at the later edge;
sixteen selects both edges. Each case directory contains `case.json`,
`control.yaml` and `fault.yaml`. The control and fault differ only in observe/hold
mode. `exploration.json` binds the selected files by SHA256. Existing output paths
are rejected; generation never starts a cluster, workload or fault.

Cases replay through the existing Sieve learning/test commands using their named
workload and generated YAML plan. The shared workload derives both handshake
positions from that name. It waits for the held UID/generation/invocation, submits
the release input, and requires completion for the exact accepted release version.
It avoids waiting for metadata convergence across the held edge, including in the
matched observe schedule. Both schedules still require final cleanup, unrelated
metadata retention, latest accepted input and fresh-state history equivalence.
A named case plan with a different declared phase pair is rejected.

Report collected execution directories against the frozen selection:

```sh
uv run python -m sieve_tool.testing.metadata_explorer report \
  --plan .cache/metadata-exploration-demo/exploration.json \
  --case /absolute/path/to/collected/control-case \
  --case /absolute/path/to/collected/fault-case \
  --output .cache/metadata-exploration-report.json
```

Each case directory is the per-workload artifact directory containing
`metadata-plan.json`. The report checks frozen input hashes and case fingerprints,
then counts actual attempts, observe controls, known fault attempts, unclassified
attempts, physically verified placements, covered operation pairs, fully observed
histories and reported semantic passes/failures separately.
A placement contributes paired coverage only with a healthy matched observe control
that observed all four inputs and all three updates, and a fault with a bound
hold/release/end witness. A physically verified fault remains counted when its
semantic checks fail or other reads are missing; these outcomes stay separate. Repeats do not multiply distinct placement coverage.
Controls containing injected-hold markers are invalid. Generated plans count as
zero executions. Unselected, tampered or incomplete proofs stay visible without
becoming covered; partial failed histories remain attempts. Cases whose initial
mode could not be recorded are explicitly unclassified and require the external
job ledger for fault-budget accounting. Physical proof also rejects altered history/placement
metadata and retains raw oracle alarms.

This report describes artifact evidence and reported semantics. Native image and
operator pin bindings, independent physical semantics, control-before-fault gating,
and bug novelty still require campaign review. Before native execution, freeze the
runtime and image provenance and set a new explicit budget. Fresh differential
validation normally needs two learning executions, one control and one fault per
selected case: at least 32 workloads/eight faults for eight cases, or 64/sixteen
for all sixteen, before failed attempts and confirmation runs. These generated
plans do not authorize those executions or reuse the exhausted prior budget.

TDD covers the public catalog/plan interfaces, both workload CLIs with external
Kubernetes/process fixtures, and the public proof/report interfaces. Tests exercise
all sixteen cases in both observe and hold modes, first-edge release before
convergence, wrong identities, stale restored metadata, incorrect worker counts,
modified case/manifest data and control pairing. These fixtures do not establish
native operator behavior for the new histories. This implementation slice launches
zero native workloads or faults. Existing native evidence, including the protected
test and all sealed checkpoints, remains preserved.
