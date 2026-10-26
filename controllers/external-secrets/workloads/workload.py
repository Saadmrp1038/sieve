"""Delete or corrupt an owned Secret and require unchanged-parent repair."""

import sys
from collections.abc import Mapping

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


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:], ExternalSecretsTarget(), timeout=180))
