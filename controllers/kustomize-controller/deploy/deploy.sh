#!/bin/sh
set -eu
kubectl apply --server-side -f kustomizations.yaml -f gitrepositories.yaml \
  -f ocirepositories.yaml -f buckets.yaml -f externalartifacts.yaml
kubectl wait --for=condition=Established --timeout=60s \
  crd/kustomizations.kustomize.toolkit.fluxcd.io \
  crd/gitrepositories.source.toolkit.fluxcd.io \
  crd/ocirepositories.source.toolkit.fluxcd.io \
  crd/buckets.source.toolkit.fluxcd.io \
  crd/externalartifacts.source.toolkit.fluxcd.io
kubectl apply -f rbac.yaml -f operator.yaml
