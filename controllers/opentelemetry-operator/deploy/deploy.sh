#!/bin/sh
set -eu

curl --fail --location --max-time 120 \
  https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml \
  --output cert-manager.yaml
curl --fail --location --max-time 120 \
  https://github.com/open-telemetry/opentelemetry-operator/releases/download/v0.162.0/opentelemetry-operator.yaml \
  --output release.yaml
curl --fail --location --max-time 120 \
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-operator/989089bf5c75d78fdc6004147f97d972fc436bd4/config/crd/bases/opentelemetry.io_clusterobservabilities.yaml \
  --output clusterobservability-crd.yaml

python3 - <<'PY'
import hashlib
from pathlib import Path

import yaml

inputs = {
    "cert-manager.yaml": "c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74",
    "release.yaml": "f3c325ea93b72f696b1682aba460b2c7bb9f8ea601b5510073c62dcb824669bb",
    "clusterobservability-crd.yaml": "24476f3a7b1cc4ca7a28f766d31c5ed3476648dc037f24583b8bba03c3e09a01",
}
for name, expected in inputs.items():
    actual = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"{name} checksum mismatch: {actual}")


def replace_namespace(value):
    if isinstance(value, str):
        return value.replace("opentelemetry-operator-system", "default")
    if isinstance(value, list):
        return [replace_namespace(item) for item in value]
    if isinstance(value, dict):
        return {key: replace_namespace(item) for key, item in value.items()}
    return value


resources = []
for resource in yaml.safe_load_all(Path("release.yaml").read_text()):
    if not resource:
        continue
    if resource.get("kind") == "Namespace":
        continue
    if resource.get("kind") == "Deployment":
        continue
    resources.append(replace_namespace(resource))
Path("resources.yaml").write_text(
    yaml.safe_dump_all(resources, sort_keys=False)
)
PY

kubectl apply --server-side -f cert-manager.yaml
kubectl rollout status deployment/cert-manager --namespace cert-manager --timeout=300s
kubectl rollout status deployment/cert-manager-webhook --namespace cert-manager --timeout=300s
kubectl rollout status deployment/cert-manager-cainjector --namespace cert-manager --timeout=300s

kubectl apply --server-side -f resources.yaml
kubectl apply --server-side -f clusterobservability-crd.yaml
kubectl wait --namespace default certificate/opentelemetry-operator-serving-cert \
  --for=condition=Ready --timeout=240s
kubectl apply -f operator.yaml
kubectl rollout status deployment/opentelemetry-operator --namespace default --timeout=300s

python3 - <<'PY'
import json
from pathlib import Path

from sieve_tool.process import execution
from sieve_tool.runtime.cluster import pull_node_image

images = [
    "ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector-k8s:0.162.0",
    "ghcr.io/open-telemetry/opentelemetry-operator/target-allocator:0.162.0",
]
with execution(log_dir=Path.cwd() / "operand-image-load-commands"):
    resolved = {image: pull_node_image(image) for image in images}
Path("node-operand-images.json").write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n"
)
PY
