"""Map bounded state histories onto the credential-free fake provider."""

import base64

from updates import VALUES, setup

from sieve_tool.testing.child_repair import Resource, Wait, get, ready
from sieve_tool.testing.sequence_workload import SequenceObservation, SequenceState, run


class ExternalSecretSequences:
    operator = "external-secrets"
    parent_kind = "externalsecret"
    name = "sieve-external-secret"
    field_path = "/spec/data/0/remoteRef/version"
    worker_argument = "--concurrent=10"
    states = {s: SequenceState(f"v{i + 1}", VALUES[i]) for i, s in enumerate("abc")}

    def setup(self, wait: Wait) -> Resource:
        setup()
        parent = get(self.parent_kind, self.name)
        assert parent is not None
        return parent

    def observe(self) -> SequenceObservation:
        parent, child = get(self.parent_kind, self.name), get("secret", self.name)
        value = None
        if child is not None:
            try:
                value = base64.b64decode(
                    child.get("data", {}).get("marker", ""), validate=True
                ).decode()
            except ValueError, UnicodeDecodeError:
                pass
        generation = None
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
    return run(arguments, ExternalSecretSequences())
