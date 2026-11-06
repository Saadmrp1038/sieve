#!/bin/sh
set -eu

export GOTOOLCHAIN=go1.26.0 GOWORK=off CGO_ENABLED=0
subject_revision=${SIEVE_SUBJECT_COMMIT:-$(git rev-parse HEAD)}

GOOS=linux GOARCH=amd64 go build -trimpath \
  -ldflags "-s -w \
    -X github.com/prometheus/common/version.Version=0.94.2 \
    -X github.com/prometheus/common/version.Revision=${subject_revision} \
    -X github.com/prometheus/common/version.Branch=release-0.94 \
    -X github.com/prometheus/common/version.BuildUser=sieve \
    -X github.com/prometheus/common/version.BuildDate=20261022-151603" \
  -o operator ./cmd/operator

docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -f Dockerfile -t sieve-prometheus-operator:latest .
