#!/bin/sh
set -eu

python3 - <<'PY'
import json
from pathlib import Path

from sieve_tool.process import execution
from sieve_tool.runtime.cluster import pull_node_image

images = (
    "quay.io/prometheus-operator/prometheus-config-reloader@sha256:a1897c67478bc23434534e91ad3855280771196b1daf17144db9dbcb655bef71",
    "quay.io/prometheus/prometheus@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0",
)
with execution(log_dir=Path.cwd() / "operand-image-load-commands"):
    resolved = {image: pull_node_image(image) for image in images}
Path("node-operand-images.json").write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n"
)
PY

curl --fail --location --max-time 120 \
  https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/0db90444c4e6fa5672be5bf7d2b15ca9dd1c5ace/bundle.yaml \
  --output bundle.yaml

python3 - <<'PY'
import hashlib
import os
import re
from pathlib import Path

source = Path("bundle.yaml")
expected = os.environ.get(
    "SIEVE_PROMETHEUS_BUNDLE_SHA256",
    "045754c91469cccb27d7832168a5b5f0448188bcb3513f74a13c76c9079d654b",
)
actual = hashlib.sha256(source.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"Prometheus Operator bundle checksum mismatch: {actual}")

documents = []
for document in re.split(r"(?m)^---\s*$\n?", source.read_text()):
    if not document.strip():
        continue
    if re.search(r"(?m)^kind:\s*Deployment\s*$", document) and re.search(
        r"(?m)^  name:\s*prometheus-operator\s*$", document
    ):
        continue
    documents.append(document.lstrip("\n"))
Path("resources.yaml").write_text(
    "".join("---\n" + document for document in documents)
)
PY

kubectl apply --server-side -f resources.yaml
kubectl apply -f operator.yaml
