#!/bin/sh
set -eu
docker build --build-arg SIEVE_BUILD_PLATFORM="${SIEVE_BUILD_PLATFORM:-linux/amd64}" -t kubernetes-secret-generator:latest -f build/Dockerfile .
