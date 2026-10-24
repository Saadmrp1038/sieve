#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ansible-playbook docker.yaml -i ansible_hosts
ansible-playbook go.yaml -i ansible_hosts
# Install Python dependencies with `uv sync --locked` from the repository root.
ansible-playbook kind.yaml -i ansible_hosts
ansible-playbook kubectl.yaml -i ansible_hosts
ansible-playbook helm.yaml -i ansible_hosts
