#!/bin/sh
set -eu

fetch() {
  url=$1
  output=$2
  expected=$3
  curl --fail --location --max-time 120 "$url" --output "$output"
  actual=$(sha256sum "$output" | awk '{print $1}')
  if [ "$actual" != "$expected" ]; then
    echo "checksum mismatch for $output: $actual" >&2
    exit 1
  fi
}

fetch \
  https://raw.githubusercontent.com/fluxcd/helm-controller/da037f220ee3e97eb91a70506b5c5f169e46bb68/config/crd/bases/helm.toolkit.fluxcd.io_helmreleases.yaml \
  helmreleases.yaml \
  6e2893d6de50837122a683d2b39de5db0f09f4a40176df3c206c66f2e1a68df1
fetch \
  https://raw.githubusercontent.com/fluxcd/source-controller/v1.10.0/config/crd/bases/source.toolkit.fluxcd.io_helmcharts.yaml \
  helmcharts.yaml \
  413fe7367bdb81324b978091abc9081fe2241bb49e3aabf06aff83c86c95a4e3
fetch \
  https://raw.githubusercontent.com/fluxcd/source-controller/v1.10.0/config/crd/bases/source.toolkit.fluxcd.io_ocirepositories.yaml \
  ocirepositories.yaml \
  dcb7198a1c747ad96b8ca5218d2780896800306098497159fa77ecd7aba9a886
fetch \
  https://raw.githubusercontent.com/fluxcd/source-controller/v1.10.0/config/crd/bases/source.toolkit.fluxcd.io_externalartifacts.yaml \
  externalartifacts.yaml \
  f78536e2c0c6719221adff02da257bf3d2dc20dcd9636ce304656fa3f320c102

kubectl apply --server-side -f helmreleases.yaml -f helmcharts.yaml \
  -f ocirepositories.yaml -f externalartifacts.yaml
kubectl wait --for=condition=Established --timeout=60s \
  crd/helmreleases.helm.toolkit.fluxcd.io \
  crd/helmcharts.source.toolkit.fluxcd.io \
  crd/ocirepositories.source.toolkit.fluxcd.io \
  crd/externalartifacts.source.toolkit.fluxcd.io
kubectl apply -f rbac.yaml -f operator.yaml
