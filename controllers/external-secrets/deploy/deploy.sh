#!/bin/sh
set -eu
curl --fail --location --max-time 120 --retry 2 \
  https://github.com/external-secrets/external-secrets/releases/download/helm-chart-2.14.0/external-secrets-2.14.0.tgz \
  --output chart.tgz
python3 - <<'PY'
import hashlib
from pathlib import Path

if hashlib.sha256(Path("chart.tgz").read_bytes()).hexdigest() != "0a6d9e91b1ff50adfdbf24b50b582babfa7c5b7df42b21974ca4b3fb88ee5e34":
    raise SystemExit("External Secrets release chart checksum mismatch")
PY
helm template external-secrets chart.tgz --namespace default --include-crds > rendered.yaml
python3 - <<'PY'
from pathlib import Path
import yaml

resources = [
    resource for resource in yaml.safe_load_all(Path("rendered.yaml").read_text())
    if resource and not (resource.get("kind") == "Deployment" and resource["metadata"]["name"] == "external-secrets")
]
Path("resources.yaml").write_text(yaml.safe_dump_all(resources))
PY
kubectl apply --server-side -f resources.yaml
kubectl rollout status deployment/external-secrets-webhook --timeout=240s
kubectl rollout status deployment/external-secrets-cert-controller --timeout=240s
kubectl apply -f operator.yaml
