# Development

Sieve uses one uv project for the Python harness and the existing Go workspace
for its client, coordinator, and instrumenter. Full Kubernetes experiments run
on an amd64 Linux machine; Python tests and style checks also run on macOS.

## Python environment

[Install uv](https://docs.astral.sh/uv/getting-started/installation/) using the
version required by `[tool.uv].required-version` in `pyproject.toml`, then run
these commands from the repository root:

```sh
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

uv selects the Python version from `.python-version` and creates an ignored
`.venv`. The current Python 3.10 baseline preserves the validated legacy SDK
environment while the harness is modernized; it is not a new long-term Python
support policy. Runtime versions are declared in `pyproject.toml`, and
`uv.lock` records their resolved dependencies for Linux and macOS. Development
tools live in the `dev` dependency group. The source is still run through its
existing repository scripts; packaging and CLI reorganization are separate
migration steps.

Prefix existing Python commands with `uv run --locked`, for example:

```sh
uv run --locked python sieve.py --help
```

Builds and experiments still need Docker, Go, kind, kubectl, `GOPATH`, and
`KUBECONFIG` as described in the main README. No Docker daemon is needed for the
Python unit and style checks above. Keep generated workspaces, downloaded
sources, and raw experiment evidence outside the committed source files.

## Formatting and linting

Ruff handles both formatting and linting. Run `./format.sh` to format the
repository, or `./format.sh --check` to check formatting. The initial lint
rules cover syntax errors and definite correctness issues; broader style and
import cleanup can be introduced as the legacy modules are refactored.
Generated build trees and local experiment scripts are excluded.

pytest collects only `tests/`. Controller workloads under `examples/` run
through Sieve, not pytest.

With Go installed, run:

```sh
(cd sieve_client && go test -race ./...)
(cd sieve_instrumentation && go test ./...)
```

## Dependency changes

Change runtime dependencies in `pyproject.toml` and development dependencies in
`[dependency-groups].dev`, then update the lockfile and run the checks above:

```sh
uv lock
uv sync --locked
```

`pyproject.toml` and `uv.lock` are the only Python dependency sources. Runtime
jobs use `uv sync --locked --no-dev` and `uv run --locked --no-dev python ...`;
development checks use the default environment with its development tools.

When updating the uv version requirement, refresh the lockfile and rerun
the checks. The setup-uv action reads the required version from `pyproject.toml`
so CI and local development use the same tool version.
