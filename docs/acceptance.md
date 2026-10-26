# Acceptance validation

The maintained suite exercises Mittwald Secret Generator v3.5.0 at the profile's
pinned commit and Kubernetes v1.24.10. It uses the existing fault behavior and
[frozen schedules](../fixtures/acceptance/secret-generator/README.md).
`smoke` runs one restart and one missed-state schedule; `full` runs five and seven.
Both execute a vanilla workload and learn two fresh baselines first. The suite is
a positive regression check: any raw alarm, failed workload, ineffective fault or
missing evidence fails acceptance. Ordinary bug-finding commands are unchanged.

## Linux host and image cache

Use a clean Git checkout and its editable Python installation (`uv sync --locked`).
Execution requires Linux, Docker, Go, kind and kubectl; `sieve doctor` checks the
host tools. The validated VM uses Docker 29.1.3, kubectl 1.24.10 and kind 0.17.0
with its existing Docker-29 image-load compatibility patch. A different kind/node
image combination needs its own validation. The command does not install or
restart host services. Offline verification also works on macOS.

Build the three node images and three controller images once:

```sh
uv run --locked sieve acceptance build-images \
  --expected-revision FULL_COMMIT_SHA \
  --registry sieve-acceptance \
  --output .cache/acceptance-images.json
```

No images are pushed. The manifest records their immutable Docker IDs, build
revision and tracked input hashes (including executable bits). Inputs cover
client hooks, instrumenter, Go workspace, pinned controller/build assets, host
build implementation, Docker routing, configuration defaults and dependency lock.
Runtime-only changes can reuse this cache. Changes to these build inputs require
new images and a fresh manifest. Existing manifests/build-log directories are
never overwritten. Build failures keep their logs beside the requested manifest.

A manifest is an attestation from a trusted build, not a signature or proof
against a malicious Docker administrator. Keep it with build evidence and do not
relabel unrelated images as compatible. The runner verifies both source-input
hashes and current Docker IDs before use. Per-run aliases are created from the
immutable IDs, insulating execution from concurrent changes to cache tags.

## Run and verify

```sh
uv run --locked sieve acceptance run \
  --expected-revision FULL_COMMIT_SHA \
  --images .cache/acceptance-images.json \
  --suite full --output runs/acceptance/example

uv run --locked sieve acceptance verify --results runs/acceptance/example
```

`--expected-revision` is required and must identify actual clean `HEAD`, not a
branch/tag or abbreviated hash. The command also checks the imported package
belongs to that checkout. Fresh outputs are mandatory; inside the checkout they
must be under `runs/`. Reference artifacts stay inside this output, so acceptance
does not overwrite other experiments' oracles.

Only registry/timeouts may differ from the packaged common settings; disabling
oracle checks or changing masks is rejected. Use ordinary `--config` overrides
for larger process/workload budgets. Parent phase deadlines include compilation,
cluster, workload and cleanup allowances; learning receives two passes' budgets.
The supervising process allows the child's cleanup/request/termination budget
before forced termination. No smaller per-fault limits are introduced.

The verifier checks raw test results, exact plan bytes, successful child outcomes,
workload finish markers, final Secret ownership/status and CA capture. Restarts
require distinct controller pod UIDs in API evidence plus a Ready replacement in
the resource snapshot. Missed states require matching held/released reads in the
requested scope. Coordinator completion or CLI exit zero alone is insufficient.
The verifier reads evidence without modifying it; `--output FILE` writes a new
verification report when requested. It never suppresses or rewrites raw alarms.

`run.json` records source revision/tree, image provenance and phase exit statuses.
`verification.json` contains independently derived counts and per-plan evidence.
`DONE` is written only after verification or final failure/cancellation. Logs,
raw results, both baselines and references remain available after failures.
The temporary cluster kubeconfig stays in the ignored private cache outside
uploaded evidence and is removed
when the supervising command exits.
Only this run's unique image aliases are removed; cache images are retained.
Sieve owns cluster/build-helper cleanup and refuses a pre-existing `kind` cluster.
A host loss or forced kill can require inspection of saved ownership records;
there is no unconditional shared-cluster deletion or daemon prune fallback.

## On-demand GitHub workflow

After the workflow is merged onto the default branch, dispatch
[acceptance.yml](../.github/workflows/acceptance.yml) against the desired branch.
It checks out and verifies the event's exact commit, runs the same command, then
runs the offline verifier again and uploads raw evidence even on failure.
It does not run on untrusted pull-request events. PR checks continue to run fast
unit/contract tests and operator image builds; they do not claim live fault runs.

Provide a dedicated runner labeled `self-hosted`, `linux`, `x64`, and
`sieve-acceptance` with compatible tools. Runner registration is an administrator
step; this repository does not register a host or install a background service.
With cache reuse, pass the absolute manifest path on that host. With
`build-images=true`, the workflow builds a fresh manifest in the runner's temp
directory. The replay has a two-hour job bound; opted-in image builds allow six
hours for the six independently bounded builds and replay. Healthy full replays
have taken about thirty minutes on the existing VM.

```sh
gh workflow run acceptance.yml --repo Saadmrp1038/sieve --ref main \
  -f suite=full -f images-manifest=/absolute/path/acceptance-images.json
```

The workflow preserves Docker caches and uses minimal repository permissions.
Only one acceptance job is active at a time, and a new dispatch does not cancel
it. The CLI's existing host lock also protects individual experiments. Do not run other kind batches on the dedicated host
while acceptance is executing.
