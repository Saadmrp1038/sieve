#!/bin/sh
set -eu
export GOTOOLCHAIN=go1.26.10 GOWORK=off CGO_ENABLED=0
# Replacements in a dependency's go.mod do not propagate to this binary module.
go -C cmd/controller mod edit \
  -require=sieve.client@v0.0.0 \
  -replace=sieve.client=../../sieve-dependency/src/sieve.client \
  -replace=k8s.io/client-go=../../sieve-dependency/src/k8s.io/client-go@v0.36.2
mkdir -p _bin/sieve-image
GOOS=linux GOARCH=amd64 go -C cmd/controller build -mod=mod -trimpath \
  -ldflags '-w -s -X github.com/cert-manager/cert-manager/pkg/util.AppVersion=v1.21.3 -X github.com/cert-manager/cert-manager/pkg/util.AppGitCommit=925c22283de556172d48d96a165dc2fa27f7a6ff' \
  -o ../../_bin/sieve-image/controller main.go
cp cmd/controller/LICENSE _bin/sieve-image/cert-manager.license
cp cmd/controller/LICENSES _bin/sieve-image/cert-manager.licenses_notice
docker build --platform "${SIEVE_BUILD_PLATFORM:-linux/amd64}" \
  --build-arg BASE_IMAGE=gcr.io/distroless/static-debian13@sha256:d4df213e27178b79e05ee2bdf8a23d8c54baab3b5a0dc083b039ad9929fa4219 \
  -f hack/containers/Containerfile.controller -t sieve-cert-manager:latest _bin/sieve-image
