# Development

The Python harness is an installed `src/` package managed by uv. The Go
workspace contains three modules. Kubernetes experiments need an amd64 Linux
host; unit and style checks also run on macOS without Docker.

## Setup and checks

Install the uv version required by `pyproject.toml`, then run from the checkout:

```sh
uv sync --locked
uv run --locked sieve --help
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv build --wheel
uv run --locked pip-audit --local --skip-editable
(cd go/client && GOWORK=off go test -race ./...)
(cd go/coordinator && GOWORK=off go test -race ./...)
(cd go/instrumenter && GOWORK=off go test -race ./...)
```

`uv sync` installs the CLI and package in editable mode. pytest imports that
package; it does not add the repository root to `sys.path`. Workloads under
`controllers/` are integration programs, separate from pytest tests.

The host harness uses Python 3.14.9, uv 0.13.5, and Go 1.27.3.
The Go toolchain is selected by `go.work`; CI reads that file too.
Controller Dockerfiles retain their experiment-specific compilers and SDKs.
The injected client has no third-party dependencies and retains a Go 1.13
language floor so adding hooks does not force a test subject upgrade.
Runtime dependencies live in `pyproject.toml`;
`uv.lock` records their Linux and macOS resolutions. To change dependencies,
edit the project metadata, run `uv lock`, then repeat the checks. Runtime jobs
use `uv sync --locked --no-dev` and `uv run --locked --no-dev sieve ...`.

Audit Go modules independently with `GOWORK=off govulncheck ./...` in each
module. CI pins the scanner version and verifies that `go/client` has no
third-party modules. Do not add a Kubernetes SDK to that module; cover metadata
adaptations with the real-object contract test in `go/coordinator`.

For changes to SDKs, serialization or oracle dependencies, build new vanilla,
learn and test images on Linux. Check the no-fault workload, learn two fresh
baselines, and replay known schedules against those baselines. Inspect actual
pod restarts or blocked/released reads and raw alarms in the saved evidence.
Also replay retained successful and failed runs: a green build or a new set
of masks alone does not validate an upgrade.

## Working on the harness

See [architecture](docs/architecture.md) for package boundaries. Keep controller
assets under `controllers/<name>/` and historical reference data under
`fixtures/`. Do not write generated files into either directory. Downloads,
controller checkouts, compiled binaries, and temporary configuration belong in
`.cache/`; new run evidence belongs in `runs/`. Local settings belong in the
ignored `configs/local.json`.

The CLI discovers the checkout from the working directory or its editable
installation. Use `sieve --repo /path/to/sieve ...` when installed from a wheel
or when selecting another checkout. Paths supplied with `--output` are
relative to the caller. Commands restore the caller's working directory even
when setup fails.

`./format.sh` formats Python with Ruff; `./format.sh --check` checks it.
Regenerate the four generated workflows after changing `sieve_tool.ci`:

```sh
uv run --locked sieve ci generate
```

A new controller starts with `uv run --locked sieve controller init NAME`.
Complete its profile, image build, deployment, and workload using the
[porting guide](docs/port.md). Run image and fault validation on Linux, using
fresh output directories so prior evidence is preserved.

Process ownership, timeout configuration and failure artifacts are described in
[process execution](docs/process-execution.md). New external commands use
`sieve_tool.process`; use argument lists and explicit `cwd`, and keep SDK calls
inside their deadline guards. Lifecycle tests run without Docker; daemon-side
cancellation controls and operator validation run on Linux.

## Contracts and regression fixtures

Fault plans use the existing YAML field names. `sieve_tool.plans` validates them
before cluster setup; a batch validates every input before starting its first
plan. The coordinator independently decodes typed specifications and returns
field-specific errors. Unknown fields, duplicate YAML keys, invalid expressions
and wrong scalar types fail at these boundaries. Historical `actions: null`
plans remain unfaulted schedules. Generators construct typed actions and retain
their existing occurrence counts and trigger expressions.

Reviewed contract fixtures live in `fixtures/plans/`. The generation snapshots
were captured before the refactor; do not regenerate them to make a failing
behavior check pass. Instrumentation fixtures under `go/instrumenter/testdata/`
check emitted source and execute it against the actual injected client offline.
Their README describes deliberate snapshot updates.

CI enforces explicit imports, Pyflakes, Bugbear, import ordering and formatting
through Ruff. Mypy checks plan/configuration/path boundaries strictly; the older
analysis and oracle internals are not yet fully typed. All three Go modules run
race checks and `go vet`. Extend the typed boundary checks when moving another
module to a complete typed interface; avoid blanket suppressions.

A controller's scaffold intentionally has unfinished repository/version fields.
Fill those fields before loading or building the profile. Controller settings
and workload-specific policies are validated, including nested fields.

Coordinator Kubernetes calls use request limits and cancellable action contexts.
Ordinary restarts wait for Ready pods. A reconnect to a paused API retains the
Running milestone, so it can finish and allow the schedule's resume trigger.
Intentional pauses have no additional timeout; cancellation remains tied to the
experiment's lifecycle.
