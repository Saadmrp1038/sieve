"""Require unchanged-parent repair and valid TLS data from cert-manager."""

import base64
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory

from sieve_tool.process import Result
from sieve_tool.process import run as run_process
from sieve_tool.testing.child_repair import (
    Mutation,
    Observation,
    Resource,
    Wait,
    get,
    kubectl,
    mutate_secret,
    ready,
    run,
)


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
        parent = observation["parent_after"]
        if (
            not ready(parent)
            or child is None
            or any(
                condition.get("type") == "Issuing"
                and condition.get("status") == "True"
                for condition in (parent or {}).get("status", {}).get("conditions", [])
            )
        ):
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
                    cwd=directory,
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


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv, CertManagerTarget(), timeout=180)


if __name__ == "__main__":
    raise SystemExit(main())
