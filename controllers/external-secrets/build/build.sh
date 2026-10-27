#!/bin/sh
set -eu
# The pinned release already includes generated code and CRDs.
make build-amd64 --assume-old=generate
docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  --build-arg TARGETOS=linux --build-arg TARGETARCH=amd64 \
  -t sieve-external-secrets:latest -f Dockerfile .
