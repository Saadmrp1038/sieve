"""Create controller integrations in the current source layout."""

import json
from pathlib import Path
import re


def create(directory, name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError(
            "Controller name must contain only letters, digits, '.', '_' or '-'"
        )
    root = Path(directory) / name
    root.mkdir(parents=True, exist_ok=False)
    for folder in ("build", "deploy", "workloads"):
        (root / folder).mkdir()
    for relative in ("build/build.sh", "deploy/deploy.sh"):
        script = root / relative
        script.write_text(
            "#!/bin/sh\nset -eu\n# Add the controller-specific commands here.\n"
        )
        script.chmod(0o755)
    config = {
        "name": name,
        "github_link": "",
        "commit": "",
        "kubernetes_version": "v1.24.10",
        "client_go_version": "",
        "dockerfile_path": "build/Dockerfile",
        "controller_image_name": f"{name}:latest",
        "annotated_reconcile_functions": {},
        "test_command": ["python3", f"controllers/{name}/workloads/workload.py"],
        "custom_resource_definitions": [],
        "controller_pod_label": name,
        "controller_deployment_file_path": f"controllers/{name}/deploy/operator.yaml",
        "test_setting": {},
    }
    (root / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    (root / "build/Dockerfile").write_text(
        "# Copy and adapt the controller's Dockerfile, including sieve-dependency/.\n"
    )
    (root / "workloads/workload.py").write_text(
        'raise SystemExit("Implement the controller workload")\n'
    )
    print(root)
