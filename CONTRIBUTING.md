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
uv build --wheel
(cd go/client && GOWORK=off go test -race ./...)
(cd go/coordinator && GOWORK=off go test ./...)
(cd go/instrumenter && GOWORK=off go test ./...)
```

`uv sync` installs the CLI and package in editable mode. pytest imports that
package; it does not add the repository root to `sys.path`. Workloads under
`controllers/` are integration programs, separate from pytest tests.

The host harness uses Python 3.14.9, uv 0.13.5, and Go 1.27.3.
The Go toolchain is selected by `go.work`; CI reads that file too.
Controller Dockerfiles retain their experiment-specific compilers and SDKs.
The injected client has no third-party dependencies and retains a Go 1.13
language floor so adding hooks does not force a test subject upgrade. Runtime dependencies live in `pyproject.toml`;
`uv.lock` records their Linux and macOS resolutions. To change dependencies,
edit the project metadata, run `uv lock`, then repeat the checks. Runtime jobs
use `uv sync --locked --no-dev` and `uv run --locked --no-dev sieve ...`.

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
