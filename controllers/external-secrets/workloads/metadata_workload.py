"""Metadata lifetime adapter for External Secrets' managed Secret template."""

import base64
import json

from sieve_tool.testing.child_repair import Resource, Wait, get, kubectl, ready
from sieve_tool.testing.metadata_workload import run
from sieve_tool.testing.sequence_workload import SequenceObservation


class ExternalSecretMetadata:
    operator = "external-secrets"
    parent_kind = "externalsecret"
    metadata_path = "/spec/target/template/metadata"
    worker_argument = "--concurrent=10"
    effective_value = "expected-value"

    def __init__(self) -> None:
        self.dependency: Resource | None = None

    def setup(
        self, wait: Wait, *, name: str, metadata: dict[str, dict[str, str]]
    ) -> Resource:
        store = "sieve-metadata-store"
        current = get("secretstore", store)
        if self.dependency is None:
            if current is not None:
                raise RuntimeError("metadata dependency already exists")
            self.dependency = json.loads(
                kubectl(
                    "create",
                    "-f",
                    "-",
                    "-o",
                    "json",
                    payload={
                        "apiVersion": "external-secrets.io/v1",
                        "kind": "SecretStore",
                        "metadata": {"name": store, "namespace": "default"},
                        "spec": {
                            "provider": {
                                "fake": {
                                    "data": [
                                        {
                                            "key": "/sieve/value",
                                            "value": self.effective_value,
                                            "version": "v1",
                                        }
                                    ]
                                }
                            }
                        },
                    },
                )
            )
        elif (
            current is None
            or current["metadata"]["uid"] != self.dependency["metadata"]["uid"]
            or current["spec"] != self.dependency["spec"]
        ):
            raise RuntimeError("metadata dependency changed")
        wait(lambda: ready(get("secretstore", store)), "fake metadata store ready")
        response: Resource = json.loads(
            kubectl(
                "create",
                "-f",
                "-",
                "-o",
                "json",
                payload={
                    "apiVersion": "external-secrets.io/v1",
                    "kind": "ExternalSecret",
                    "metadata": {"name": name, "namespace": "default", "labels": {}},
                    "spec": {
                        "refreshPolicy": "Periodic",
                        "refreshInterval": "5s",
                        "secretStoreRef": {"kind": "SecretStore", "name": store},
                        "target": {
                            "name": name,
                            "creationPolicy": "Owner",
                            "template": {"metadata": metadata},
                        },
                        "data": [
                            {
                                "secretKey": "marker",
                                "remoteRef": {"key": "/sieve/value", "version": "v1"},
                            }
                        ],
                    },
                },
            )
        )
        return response

    def observe(self, name: str) -> SequenceObservation:
        parent, child = get(self.parent_kind, name), get("secret", name)
        value, generation = None, None
        if child is not None:
            try:
                value = base64.b64decode(
                    child.get("data", {}).get("marker", ""), validate=True
                ).decode()
            except ValueError, UnicodeDecodeError:
                pass
        if (
            parent is not None
            and ready(parent)
            and parent.get("status", {})
            .get("syncedResourceVersion", "")
            .startswith(str(parent["metadata"]["generation"]) + "-")
        ):
            generation = parent["metadata"]["generation"]
        return SequenceObservation(parent, child, value, generation)


def main(arguments: list[str]) -> int:
    return run(arguments, ExternalSecretMetadata())
