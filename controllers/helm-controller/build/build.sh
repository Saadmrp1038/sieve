#!/bin/sh
set -eu

export GOTOOLCHAIN=go1.27.0 GOWORK=off CGO_ENABLED=0
GOOS=linux GOARCH=amd64 go build -trimpath -a -o bin/helm-controller main.go
docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -t sieve-helm-controller:latest -f Dockerfile .
