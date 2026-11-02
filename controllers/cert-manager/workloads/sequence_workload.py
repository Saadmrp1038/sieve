"""Map bounded state histories onto SelfSigned certificate DNS declarations."""

import base64
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

from sieve_tool.process import run as run_process
from sieve_tool.testing.child_repair import RepairTarget, Resource, Wait, get
from sieve_tool.testing.sequence_workload import SequenceObservation, SequenceState, run


class CertificateReader(RepairTarget, Protocol):
    name: str
    hostname: str


class CertificateSequences:
    operator = "cert-manager"
    parent_kind = "certificate"
    field_path = "/spec/dnsNames"
    worker_argument = "--concurrent-workers=10"

    def __init__(self, target: CertificateReader) -> None:
        self.target, self.name = target, target.name
        self.states = {
            state: SequenceState([host], host)
            for state, host in zip(
                "abc",
                (target.hostname, "sequence-b.example.test", "sequence-c.example.test"),
                strict=True,
            )
        }

    def setup(self, wait: Wait) -> Resource:
        self.target.setup(wait)
        parent = get(self.parent_kind, self.name)
        assert parent is not None
        return parent

    def observe(self) -> SequenceObservation:
        observation = self.target.observe()
        parent, child = observation["parent_after"], observation["child_after"]
        generation = None
        if parent is not None:
            conditions = parent.get("status", {}).get("conditions", [])
            if any(
                c.get("type") == "Ready"
                and c.get("status") == "True"
                and c.get("observedGeneration") == parent["metadata"]["generation"]
                for c in conditions
            ) and not any(
                c.get("type") == "Issuing" and c.get("status") == "True"
                for c in conditions
            ):
                generation = parent["metadata"]["generation"]
        value = None
        if generation is not None and child is not None and parent is not None:
            names = parent["spec"].get("dnsNames", [])
            if len(names) == 1:
                self.target.hostname = names[0]
                if self.target.healthy(observation):
                    with TemporaryDirectory(prefix="sieve-sequence-cert-") as directory:
                        path = Path(directory) / "certificate.pem"
                        path.write_bytes(
                            base64.b64decode(child["data"]["tls.crt"], validate=True)
                        )
                        result = run_process(
                            [
                                "openssl",
                                "x509",
                                "-in",
                                str(path),
                                "-noout",
                                "-ext",
                                "subjectAltName",
                            ],
                            capture_output=True,
                            category="request",
                            operation="read issued certificate names",
                            check=False,
                        )
                        found = re.findall(r"(?:^|[,\s])DNS:([^,\s]+)", result.stdout)
                        if result.returncode == 0 and len(found) == 1:
                            value = found[0]
        return SequenceObservation(parent, child, value, generation)


def main(arguments: list[str], target: CertificateReader) -> int:
    return run(arguments, CertificateSequences(target))
