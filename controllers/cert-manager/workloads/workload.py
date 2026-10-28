"""Require unchanged-parent repair and valid TLS data from cert-manager."""

import argparse
import base64
import json
import math
import sys
import time
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
    owned_by,
    ready,
    run,
)
from sieve_tool.testing.updates import IndependentUpdates


class CertManagerTarget:
    name = "sieve-certificate"
    hostname = "managed-child.example.test"
    expected_crl_url: str | None = None

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
                condition.get("type") == "Issuing" and condition.get("status") == "True"
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
            if self.expected_crl_url is not None:
                detail = openssl("x509", "-in", str(cert_path), "-text", "-noout")
                if detail.returncode != 0 or self.expected_crl_url not in detail.stdout:
                    return False
            cert_public = openssl("x509", "-in", str(cert_path), "-pubkey", "-noout")
            key_public = openssl("pkey", "-in", str(key_path), "-pubout")
            return (
                cert_public.returncode == key_public.returncode == 0
                and cert_public.stdout.strip() == key_public.stdout.strip()
            )

    def mutate(self, workload: str, child: Resource) -> Mutation:
        return mutate_secret(self.name, workload, child, "tls.crt")


class IndependentTarget(CertManagerTarget):
    """Supply overwrites without waiting for any reconciliation between them."""

    def __init__(self, workload: str, output: Path, interval: float) -> None:
        self.workload, self.output, self.interval = workload, output, interval
        self.mode = workload.split("-miss-", 1)[1].removesuffix("-updates")
        self.initial_parent: Resource | None = None
        self.expected_parent: Resource | None = None
        self.source: Resource = {
            "schema_version": 1,
            "workload": workload,
            "status": "in_progress",
            "updates": [],
        }

    def save_updates(self, **evidence: object) -> None:
        self.output.with_name("workload-updates.json").write_text(
            json.dumps(self.source, indent=2) + "\n"
        )
        self.output.with_name("update-observations.json").write_text(
            json.dumps(
                {
                    "workload": self.workload,
                    "initial_parent": self.initial_parent,
                    "expected_parent": self.expected_parent,
                    **evidence,
                },
                indent=2,
            )
            + "\n"
        )

    def setup(self, wait: Wait) -> None:
        try:
            super().setup(wait)

            def healthy() -> bool:
                observation = self.observe()
                parent, child = observation["parent_after"], observation["child_after"]
                return (
                    parent is not None
                    and child is not None
                    and owned_by(child, parent)
                    and self.healthy(observation)
                )

            wait(healthy, "quiescent initial issuance")
            self.initial_parent = get("certificate", self.name)
            assert self.initial_parent is not None
            self.expected_parent = self.initial_parent
            resource, name, field = {
                "issuer": (
                    "issuer",
                    "sieve-selfsigned",
                    "/spec/selfSigned/crlDistributionPoints",
                ),
                "parent": ("certificate", self.name, "/spec/dnsNames"),
                "child": ("secret", self.name, "/data/tls.crt"),
            }[self.mode]
            original = get(resource, name)
            if original is None:
                raise RuntimeError("Independent input disappeared")
            uid = original["metadata"]["uid"]
            self.source["resource_key"] = f"{resource}/default/{name}"
            for index, phase in enumerate("ABCD"):
                current = get(resource, name)
                if current is None or current["metadata"]["uid"] != uid:
                    raise RuntimeError("Independent input identity changed")
                value = {
                    "issuer": [f"http://phase-{phase.lower()}.example.test/crl"],
                    "parent": [
                        CertManagerTarget.hostname,
                        f"phase-{phase.lower()}.example.test",
                    ],
                    "child": original.get("data", {}).get("tls.crt")
                    if phase in "AD"
                    else base64.b64encode(f"intermediate-{phase}".encode()).decode(),
                }[self.mode]
                label = "sieve.testing/state"
                labels_exist = "labels" in current["metadata"]
                operations = [
                    {"op": "test", "path": "/metadata/uid", "value": uid},
                    {
                        "op": "add",
                        "path": "/metadata/labels/sieve.testing~1state"
                        if labels_exist
                        else "/metadata/labels",
                        "value": phase if labels_exist else {label: phase},
                    },
                    {"op": "add", "path": field, "value": value},
                ]
                response = json.loads(
                    kubectl(
                        "patch",
                        resource,
                        name,
                        "--type=json",
                        "-p",
                        json.dumps(operations),
                        "-o",
                        "json",
                    )
                )
                section, *parts = field.strip("/").split("/")
                projected: Resource = {
                    "metadata": {"labels": {label: phase}},
                    section: {},
                }
                cursor = projected[section]
                for part in parts[:-1]:
                    cursor[part] = {}
                    cursor = cursor[part]
                cursor[parts[-1]] = value
                if response.get("metadata", {}).get("uid") != uid:
                    raise RuntimeError(
                        "Update response changed independent input identity"
                    )
                self.source["updates"].append(
                    {"producer": "workload", "state": projected, "response": response}
                )
                # Validate the accepted response, including the actual input value.
                if len(self.source["updates"]) >= 3:
                    IndependentUpdates.model_validate(
                        {**self.source, "status": "completed"}
                    )
                if self.mode == "parent":
                    self.expected_parent = response
                self.save_updates(status="updating")
                if index != 3:
                    time.sleep(self.interval)
            IndependentUpdates.model_validate({**self.source, "status": "completed"})
            self.source["status"] = "completed"
            if self.mode == "parent":
                self.hostname = "phase-d.example.test"
            wait(healthy, "convergence after independent overwrites")
            self.save_updates(
                status="converged",
                parent_after=get("certificate", self.name),
                child_after=get("secret", self.name),
            )
        except Exception as error:
            if self.source["status"] != "completed":
                self.source["status"] = "failed"
            self.save_updates(status="failed", error=str(error))
            raise

    def observe(self) -> Observation:
        observation = super().observe()
        parent = observation["parent_after"]
        if self.initial_parent is not None and parent is not None:
            if parent["metadata"]["uid"] != self.initial_parent["metadata"]["uid"]:
                raise RuntimeError("Parent identity changed during independent updates")
            # Each declared parent patch advances the expected generation/spec.
            expected = self.expected_parent
            if expected is not None and (
                parent["spec"] != expected["spec"]
                or parent["metadata"]["generation"]
                != expected["metadata"]["generation"]
            ):
                raise RuntimeError(
                    "Parent changed outside declared independent updates"
                )
        return observation

    def mutate(self, workload: str, child: Resource) -> Mutation:
        if self.mode == "issuer":
            self.expected_crl_url = "http://phase-d.example.test/crl"
        return super().mutate(workload, child)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or "-miss-" not in arguments[0]:
        return run(arguments, CertManagerTarget(), timeout=180)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "workload",
        choices=[
            f"{base}-miss-{resource}-updates"
            for base in ("delete-child", "drift-child")
            for resource in ("issuer", "parent", "child")
        ],
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--update-interval", type=float, default=8)
    args, remainder = parser.parse_known_args(arguments)
    if not math.isfinite(args.update_interval) or args.update_interval <= 0:
        parser.error("update interval must be finite and positive")
    return run(
        [args.workload.split("-miss-", 1)[0], str(args.output), *remainder],
        IndependentTarget(args.workload, args.output, args.update_interval),
        timeout=180,
    )


if __name__ == "__main__":
    raise SystemExit(main())
