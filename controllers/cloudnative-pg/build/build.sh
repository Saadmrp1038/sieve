#!/bin/sh
set -eu
export GOTOOLCHAIN=go1.27.1 GOWORK=off CGO_ENABLED=0
mkdir -p dist/manager
subject_revision=${SIEVE_SUBJECT_COMMIT:-$(git rev-parse HEAD)}
GOOS=linux GOARCH=amd64 go build -mod=mod -trimpath \
  -ldflags "-s -w -X github.com/cloudnative-pg/cloudnative-pg/pkg/versions.buildVersion=1.30.1 -X github.com/cloudnative-pg/cloudnative-pg/pkg/versions.buildCommit=$subject_revision" \
  -o dist/manager/manager_amd64 ./cmd/manager
docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -f Dockerfile -t sieve-cloudnative-pg:latest .
