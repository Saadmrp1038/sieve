#!/bin/sh
set -eu
# Reuse the same bounded, node-platform-aware loader as the runtime setup.
python3 - <<'PY'
import os
from sieve_tool.process import execution
from sieve_tool.runtime.cluster import _load_controller_image

with execution():
    for name in ("SIEVE_CNPG_POSTGRES_IMAGE", "SIEVE_CNPG_PGBOUNCER_IMAGE"):
        if image := os.environ.get(name):
            _load_controller_image(image)
PY
curl --fail --location --max-time 120 --retry 2 \
  https://github.com/cloudnative-pg/cloudnative-pg/releases/download/v1.30.1/cnpg-1.30.1.yaml \
  --output release.yaml
python3 - <<'PY'
import hashlib
from pathlib import Path
import yaml

source = Path("release.yaml")
if hashlib.sha256(source.read_bytes()).hexdigest() != "37237f145d8138256ea25ae830f87759255665ff08f8d552fdd8224a5ec032fb":
    raise SystemExit("CloudNativePG release manifest checksum mismatch")
objects = []
for obj in yaml.safe_load_all(source.read_text()):
    if not obj:
        continue
    if obj["kind"] == "Deployment" and obj["metadata"]["name"] == "cnpg-controller-manager":
        continue
    if obj["metadata"].get("namespace") == "cnpg-system":
        obj["metadata"]["namespace"] = "default"
    for subject in obj.get("subjects", []):
        if subject.get("namespace") == "cnpg-system":
            subject["namespace"] = "default"
    for webhook in obj.get("webhooks", []):
        service = webhook.get("clientConfig", {}).get("service", {})
        if service.get("namespace") == "cnpg-system":
            service["namespace"] = "default"
    conversion = obj.get("spec", {}).get("conversion", {}).get("webhook", {}).get("clientConfig", {}).get("service", {})
    if conversion.get("namespace") == "cnpg-system":
        conversion["namespace"] = "default"
    objects.append(obj)
Path("resources.yaml").write_text(yaml.safe_dump_all(objects, sort_keys=False))
PY
kubectl apply --server-side -f resources.yaml
kubectl apply -f operator.yaml
