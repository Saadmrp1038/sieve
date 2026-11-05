#!/bin/sh
set -eu
# Use the native Go minimum, dependency graph, binary flags and runtime packages.
export GOWORK=off
CGO_ENABLED=0 go build -trimpath -a -o bin/kustomize-controller main.go
docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  -t sieve-kustomize-controller:latest -f Dockerfile .
