#!/bin/sh
set -eu

export GOTOOLCHAIN=go1.26.0 GOWORK=off CGO_ENABLED=0
subject_revision=${SIEVE_SUBJECT_COMMIT:-$(git rev-parse HEAD)}

GOOS=linux GOARCH=amd64 go build -trimpath \
  -ldflags "-s -w \
    -X github.com/kedacore/keda/v2/version.GitCommit=${subject_revision} \
    -X github.com/kedacore/keda/v2/version.Version=2.21.0" \
  -o keda ./cmd/operator

docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -f Dockerfile -t sieve-keda:latest .
