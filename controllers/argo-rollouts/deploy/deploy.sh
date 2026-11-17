#!/bin/sh
set -eu

python3 - <<'PY'
import json
from pathlib import Path

from sieve_tool.process import execution
from sieve_tool.runtime.cluster import pull_node_image

image = "busybox:1.37.0"
with execution(log_dir=Path.cwd() / "operand-image-load-commands"):
    resolved = {image: pull_node_image(image)}
Path("node-operand-images.json").write_text(
    json.dumps(resolved, indent=2, sort_keys=True) + "\n"
)
PY

curl --fail --location --max-time 120 \
  https://raw.githubusercontent.com/argoproj/argo-rollouts/be8a6221c6c9751cd14497f31a2cb3c33690375a/manifests/install.yaml \
  --output install.yaml

python3 - <<'PY'
import hashlib
import os
import re
from pathlib import Path

source = Path("install.yaml")
expected = os.environ.get(
    "SIEVE_ARGO_ROLLOUTS_INSTALL_SHA256",
    "be25242c55bab7650e0b845da11dbce811f7427deae367e224435e0d766ebe5a",
)
actual = hashlib.sha256(source.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"Argo Rollouts install checksum mismatch: {actual}")

documents = []
for document in re.split(r"(?m)^---\s*$\n?", source.read_text()):
    if not document.strip():
        continue
    is_deployment = re.search(r"(?m)^kind:\s*Deployment\s*$", document)
    is_controller = re.search(r"(?m)^  name:\s*argo-rollouts\s*$", document)
    if is_deployment and is_controller:
        continue
    if re.search(r"(?m)^kind:\s*ClusterRoleBinding\s*$", document) and is_controller:
        document = re.sub(
            r"(?m)^  namespace:\s*argo-rollouts\s*$",
            "  namespace: default",
            document,
        )
    documents.append(document.lstrip("\n"))
Path("resources.yaml").write_text(
    "".join("---\n" + document.rstrip() + "\n" for document in documents)
)
PY

kubectl apply --server-side --namespace default -f resources.yaml
kubectl apply -f operator.yaml
