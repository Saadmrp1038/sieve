# External Secrets v2.14.0

This profile pins the release at `fc0dd82da6422c6ec840d24425bbda68e5259aab`.
It keeps the operator's client-go v0.37.1 and controller-runtime v0.25.2
dependencies and builds all providers with Go 1.27.3. Kubernetes v1.36.4,
kind v0.33.0 and Helm v4.3.1 are used for VM validation.

The main controller runs with the chart's default single worker. Its image,
Sieve configuration environment and API endpoint are supplied by the harness.
The webhook and certificate controller use the unchanged published image. The
chart is fetched by immutable release URL and checked against its SHA256.
`operator.yaml` was rendered from that chart with `deploy/values.yaml`.

## Workloads

- `delete-child`: delete the owned Secret with a UID precondition and require a
  replacement with a new UID.
- `drift-child`: replace the Secret's marker using a UID-tested JSON patch and
  require the original value to return.

Both workloads use a fake SecretStore and an ExternalSecret with five-second
periodic refresh and `creationPolicy: Owner`. No provider credentials are needed.
The parent UID, generation and spec must stay unchanged. Correct ownership,
Ready status and 15 seconds of stable expected data are required. Each
convergence/repair wait defaults to 180 seconds. Raw mutation responses and
repair evidence are retained on failure.

## Instrumentation

Learning hooks bracket the ExternalSecret and SecretStore reconciliations.
Client-go hooks observe individual deltas, atomic batches and initial-list
replacement before and after cache changes. Batch transactions remain atomic.
Metadata-only Secret watches retain their Kubernetes kind, and test hooks keep
cache identity after deletion so a NotFound read can still be paused.

Only observation calls and dependency wiring are added to the operator source.
Reconciliation rules, provider registration and runtime defaults are unchanged.
This pin's informer implementation has direct integration tests at the
instrumenter command and public client-go informer boundaries. Other client-go
versions require their own compatibility validation.

## Build and run

```sh
uv run --locked sieve build --kubernetes-version v1.36.4 -m learn -r sieve-lab
uv run --locked sieve build --kubernetes-version v1.36.4 -m test -r sieve-lab
uv run --locked sieve build -c external-secrets -m all -r sieve-lab
uv run --locked sieve learn -c external-secrets -w delete-child --build-oracle -r sieve-lab
uv run --locked sieve learn -c external-secrets -w drift-child --build-oracle -r sieve-lab
```

Node builds require space for the Kubernetes builder. The validation VM used
verified upstream release artifacts for the unchanged node components and
rebuilt only the instrumented API server; exact build scripts, source patches,
binary/image hashes and commands are retained with its evidence.

Run the generated YAML files with `sieve test -c external-secrets -p PATH` and
the same registry/oracle settings. Preserve raw alarms and verify actual read
holds/releases or distinct controller pod UIDs and Ready replacements. A
generated schedule is not evidence that its trigger was reached. Zero plans in
a fault family remain a coverage limitation.
