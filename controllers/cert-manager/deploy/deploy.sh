#!/bin/sh
set -eu
curl --fail --location --max-time 120 --retry 2 \
  https://github.com/cert-manager/cert-manager/releases/download/v1.21.3/cert-manager.yaml \
  --output release.yaml
python3 - <<'PY'
import copy
import hashlib
from pathlib import Path
import yaml

manifest = Path("release.yaml")
if hashlib.sha256(manifest.read_bytes()).hexdigest() != "c04a51295adda2b1a775146751f973e787190778c7f35180861d9678876e5c74":
    raise SystemExit("cert-manager release manifest checksum mismatch")
resources = []
for resource in yaml.safe_load_all(manifest.read_text()):
    if not resource:
        continue
    kind, metadata = resource["kind"], resource["metadata"]
    if kind == "Deployment" and metadata["name"] == "cert-manager":
        continue
    for subject in resource.get("subjects", []):
        if subject.get("kind") == "ServiceAccount" and subject.get("name") == "cert-manager" and subject.get("namespace") == "cert-manager":
            subject["namespace"] = "default"
    if kind == "ServiceAccount" and metadata["name"] == "cert-manager":
        account = copy.deepcopy(resource)
        account["metadata"]["namespace"] = "default"
        resources.append(account)
    if kind == "Service" and metadata["name"] == "cert-manager":
        metadata["namespace"] = "default"
    resources.append(resource)
Path("resources.yaml").write_text(yaml.safe_dump_all(resources))
PY
kubectl apply --server-side -f resources.yaml
kubectl rollout status deployment/cert-manager-webhook --namespace cert-manager --timeout=240s
kubectl rollout status deployment/cert-manager-cainjector --namespace cert-manager --timeout=240s
kubectl apply -f operator.yaml
