#!/bin/sh
set -eu

export GOTOOLCHAIN=go1.26.10 GOWORK=off CGO_ENABLED=0
subject_revision=${SIEVE_SUBJECT_COMMIT:-$(git rev-parse HEAD)}
mkdir -p bin

GOOS=linux GOARCH=amd64 go build -trimpath -a \
  -ldflags "-s -w \
    -X github.com/argoproj/argo-rollouts/utils/version.version=1.10.2 \
    -X github.com/argoproj/argo-rollouts/utils/version.buildDate=2026-11-03T15:01:38Z \
    -X github.com/argoproj/argo-rollouts/utils/version.gitCommit=${subject_revision} \
    -X github.com/argoproj/argo-rollouts/utils/version.gitTreeState=dirty" \
  -o bin/rollouts-controller ./cmd/rollouts-controller

docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -f Dockerfile -t sieve-argo-rollouts:latest .
