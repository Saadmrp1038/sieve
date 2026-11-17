#!/bin/sh
set -eu

export GOTOOLCHAIN=go1.26.3 GOWORK=off CGO_ENABLED=0
subject_revision=${SIEVE_SUBJECT_COMMIT:-$(git rev-parse HEAD)}
mkdir -p bin

GOOS=linux GOARCH=amd64 go build -trimpath -a \
  -ldflags "-s -w \
    -X github.com/open-telemetry/opentelemetry-operator/internal/version.version=0.162.0 \
    -X github.com/open-telemetry/opentelemetry-operator/internal/version.buildDate=2026-10-23T17:53:48Z \
    -X github.com/open-telemetry/opentelemetry-operator/internal/version.otelCol=0.162.0 \
    -X github.com/open-telemetry/opentelemetry-operator/internal/version.targetAllocator=0.162.0" \
  -o bin/manager_amd64 ./main.go

docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  --label "org.opencontainers.image.revision=${subject_revision}" \
  -f Dockerfile -t sieve-opentelemetry-operator:latest .
