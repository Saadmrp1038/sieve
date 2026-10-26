"""Recovery workloads for current releases; instrumentation ports are separate."""

import argparse
import base64
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from sieve_tool.process import Result
from sieve_tool.process import run as run_process
from sieve_tool.testing.child_repair import (
    Mutation,
    Observation,
    RepairTarget,
    Resource,
    Wait,
    delete_child,
    get,
    kubectl,
    run,
)


class ExternalSecretsTarget:
    name = "sieve-external-secret"

    def setup(self, wait: Wait) -> None:
        store = {
            "apiVersion": "external-secrets.io/v1",
            "kind": "SecretStore",
            "metadata": {"name": "sieve-local-store", "namespace": "default"},
            "spec": {
                "provider": {
                    "fake": {
                        "data": [
                            {
                                "key": "/sieve/value",
                                "value": "expected-value",
                                "version": "v1",
                            }
                        ]
                    }
                }
            },
        }
        kubectl("apply", "-f", "-", payload=store)
        wait(
            lambda: ready(get("secretstore", "sieve-local-store")),
            "fake SecretStore ready",
        )
        kubectl(
            "apply",
            "-f",
            "-",
            payload={
                "apiVersion": "external-secrets.io/v1",
                "kind": "ExternalSecret",
                "metadata": {"name": self.name, "namespace": "default"},
                "spec": {
                    "refreshPolicy": "Periodic",
                    "refreshInterval": "5s",
                    "secretStoreRef": {
                        "kind": "SecretStore",
                        "name": "sieve-local-store",
                    },
                    "target": {"name": self.name, "creationPolicy": "Owner"},
                    "data": [
                        {
                            "secretKey": "marker",
                            "remoteRef": {"key": "/sieve/value", "version": "v1"},
                        }
                    ],
                },
            },
        )

    def observe(self) -> Observation:
        return {
            "parent_after": get("externalsecret", self.name),
            "child_after": get("secret", self.name),
        }

    def healthy(self, observation: Mapping[str, Resource | None]) -> bool:
        parent, child = observation["parent_after"], observation["child_after"]
        return (
            ready(parent)
            and child is not None
            and child.get("data", {}).get("marker") == "ZXhwZWN0ZWQtdmFsdWU="
        )

    def mutate(self, workload: str, child: Resource) -> Mutation:
        return mutate_secret(self.name, workload, child, "marker")


class CertManagerTarget:
    name = "sieve-certificate"
    hostname = "managed-child.example.test"

    def setup(self, wait: Wait) -> None:
        kubectl(
            "apply",
            "-f",
            "-",
            payload={
                "apiVersion": "cert-manager.io/v1",
                "kind": "Issuer",
                "metadata": {"name": "sieve-selfsigned", "namespace": "default"},
                "spec": {"selfSigned": {}},
            },
        )
        wait(
            lambda: ready(get("issuer", "sieve-selfsigned")), "SelfSigned issuer ready"
        )
        kubectl(
            "apply",
            "-f",
            "-",
            payload={
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {"name": self.name, "namespace": "default"},
                "spec": {
                    "secretName": self.name,
                    "commonName": self.hostname,
                    "dnsNames": [self.hostname],
                    "issuerRef": {"name": "sieve-selfsigned", "kind": "Issuer"},
                    "privateKey": {
                        "algorithm": "ECDSA",
                        "size": 256,
                        "rotationPolicy": "Always",
                    },
                    "usages": ["digital signature", "server auth"],
                },
            },
        )

    def observe(self) -> Observation:
        return {
            "parent_after": get("certificate", self.name),
            "child_after": get("secret", self.name),
        }

    def healthy(self, observation: Mapping[str, Resource | None]) -> bool:
        child = observation["child_after"]
        if not ready(observation["parent_after"]) or child is None:
            return False
        try:
            data = child["data"]
            certificate = base64.b64decode(data["tls.crt"], validate=True)
            key = base64.b64decode(data["tls.key"], validate=True)
        except KeyError, ValueError:
            return False
        with TemporaryDirectory(prefix="sieve-certificate-") as directory:
            cert_path, key_path = (
                Path(directory) / "cert.pem",
                Path(directory) / "key.pem",
            )
            cert_path.write_bytes(certificate)
            key_path.write_bytes(key)
            key_path.chmod(0o600)

            def openssl(*args: str) -> Result:
                return run_process(
                    ["openssl", *args],
                    category="request",
                    capture_output=True,
                    operation="verify managed TLS Secret",
                    check=False,
                )

            valid = openssl(
                "verify",
                "-CAfile",
                str(cert_path),
                "-verify_hostname",
                self.hostname,
                "-purpose",
                "sslserver",
                str(cert_path),
            )
            if valid.returncode != 0:
                return False
            cert_public = openssl("x509", "-in", str(cert_path), "-pubkey", "-noout")
            key_public = openssl("pkey", "-in", str(key_path), "-pubout")
            return (
                cert_public.returncode == key_public.returncode == 0
                and cert_public.stdout.strip() == key_public.stdout.strip()
            )

    def mutate(self, workload: str, child: Resource) -> Mutation:
        return mutate_secret(self.name, workload, child, "tls.crt")


def mutate_secret(name: str, workload: str, child: Resource, field: str) -> Mutation:
    if workload == "delete-child":
        return delete_child("secrets", name, child["metadata"]["uid"])
    value = base64.b64encode(b"drift").decode()
    response = json.loads(
        kubectl(
            "patch",
            "secret",
            name,
            "--type=json",
            "-p",
            json.dumps(
                [
                    {
                        "op": "test",
                        "path": "/metadata/uid",
                        "value": child["metadata"]["uid"],
                    },
                    {"op": "replace", "path": "/data/" + field, "value": value},
                ]
            ),
            "-o",
            "json",
        )
    )
    return Mutation(response, response["data"].get(field) == value)


def ready(resource: Resource | None) -> bool:
    return resource is not None and any(
        c.get("type") == "Ready"
        and c.get("status") == "True"
        and c.get("observedGeneration", resource["metadata"].get("generation"))
        == resource["metadata"].get("generation")
        for c in resource.get("status", {}).get("conditions", [])
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operator", choices=["external-secrets", "cert-manager"])
    arguments, remaining = parser.parse_known_args(argv)
    target: RepairTarget = (
        ExternalSecretsTarget()
        if arguments.operator == "external-secrets"
        else CertManagerTarget()
    )
    return run(remaining, target, timeout=180)


if __name__ == "__main__":
    raise SystemExit(main())
