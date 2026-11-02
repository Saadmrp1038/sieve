"""Metadata lifetime adapter for cert-manager's Secret template."""

import json

from sequence_workload import CertificateReader, CertificateSequences

from sieve_tool.testing.child_repair import Resource, Wait, get, kubectl, ready
from sieve_tool.testing.metadata_workload import run
from sieve_tool.testing.sequence_workload import SequenceObservation


class CertificateMetadata:
    operator = "cert-manager"
    parent_kind = "certificate"
    metadata_path = "/spec/secretTemplate"
    worker_argument = "--concurrent-workers=10"

    def __init__(self, target: CertificateReader) -> None:
        self.target = target
        self.effective_value = target.hostname
        self.dependency: Resource | None = None

    def setup(
        self, wait: Wait, *, name: str, metadata: dict[str, dict[str, str]]
    ) -> Resource:
        issuer = "sieve-metadata-issuer"
        current = get("issuer", issuer)
        if self.dependency is None:
            if current is not None:
                raise RuntimeError("metadata dependency already exists")
            self.dependency = json.loads(
                kubectl(
                    "create",
                    "-f",
                    "-",
                    payload={
                        "apiVersion": "cert-manager.io/v1",
                        "kind": "Issuer",
                        "metadata": {"name": issuer, "namespace": "default"},
                        "spec": {"selfSigned": {}},
                    },
                )
            )
        elif (
            current is None
            or current["metadata"]["uid"] != self.dependency["metadata"]["uid"]
            or current["spec"] != self.dependency["spec"]
        ):
            raise RuntimeError("metadata dependency changed")
        wait(lambda: ready(get("issuer", issuer)), "metadata SelfSigned issuer ready")
        response: Resource = json.loads(
            kubectl(
                "create",
                "-f",
                "-",
                payload={
                    "apiVersion": "cert-manager.io/v1",
                    "kind": "Certificate",
                    "metadata": {"name": name, "namespace": "default", "labels": {}},
                    "spec": {
                        "secretName": name,
                        "secretTemplate": metadata,
                        "commonName": self.effective_value,
                        "dnsNames": [self.effective_value],
                        "issuerRef": {"name": issuer, "kind": "Issuer"},
                        "privateKey": {
                            "algorithm": "ECDSA",
                            "size": 256,
                            "rotationPolicy": "Always",
                        },
                        "usages": ["digital signature", "server auth"],
                    },
                },
            )
        )
        return response

    def observe(self, name: str) -> SequenceObservation:
        self.target.name = name
        return CertificateSequences(self.target).observe()


def main(arguments: list[str], target: CertificateReader) -> int:
    return run(arguments, CertificateMetadata(target))
