#!/bin/sh
set -eu

exec uv run --locked ruff format "$@" .
