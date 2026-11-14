#!/bin/sh
set -eu

python3 - <<'PY'
import json
from pathlib import Path

from sieve_tool.process import execution
from sieve_tool.runtime.cluster import pull_node_image

images = (
    "ghcr.io/kedacore/keda-admission-webhooks:2.21.0",
    "ghcr.io/kedacore/keda-metrics-apiserver:2.21.0",
    "registry.k8s.io/pause:3.10.1",
)
with execution(log_dir=Path.cwd() / "operand-image-load-commands"):
    resolved = {image: pull_node_image(image) for image in images}
Path("node-operand-images.json").write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n"
)
PY

curl --fail --location --max-time 120 \
  https://github.com/kedacore/keda/releases/download/v2.21.0/keda-2.21.0.yaml \
  --output release.yaml

python3 - <<'PY'
import hashlib
from pathlib import Path

import yaml

source = Path("release.yaml")
expected = "b43c89ffeef81722d7e2dd2c079d74789767a0f89cae1336cff784994814f6d7"
actual = hashlib.sha256(source.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"KEDA release manifest checksum mismatch: {actual}")

objects = []
for obj in yaml.safe_load_all(source.read_text()):
    if not obj:
        continue
    metadata = obj.get("metadata", {})
    if obj.get("kind") == "Namespace" and metadata.get("name") == "keda":
        continue
    if obj.get("kind") == "Deployment" and metadata.get("name") == "keda-operator":
        continue
    if metadata.get("namespace") == "keda":
        metadata["namespace"] = "default"
    for subject in obj.get("subjects", []):
        if subject.get("namespace") == "keda":
            subject["namespace"] = "default"
    service = obj.get("spec", {}).get("service", {})
    if service.get("namespace") == "keda":
        service["namespace"] = "default"
    for webhook in obj.get("webhooks", []):
        service = webhook.get("clientConfig", {}).get("service", {})
        if service.get("namespace") == "keda":
            service["namespace"] = "default"
    service = (
        obj.get("spec", {})
        .get("conversion", {})
        .get("webhook", {})
        .get("clientConfig", {})
        .get("service", {})
    )
    if service.get("namespace") == "keda":
        service["namespace"] = "default"
    objects.append(obj)

Path("resources.yaml").write_text(yaml.safe_dump_all(objects, sort_keys=False))
PY

kubectl apply --server-side -f resources.yaml
kubectl apply -f operator.yaml
kubectl rollout status deployment/keda-operator --timeout=300s
kubectl rollout status deployment/keda-admission --timeout=300s
kubectl rollout status deployment/keda-metrics-apiserver --timeout=300s
