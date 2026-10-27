"""Evidence that a workload independently produced a sequence of object states."""

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from sieve_tool.events import Object
from sieve_tool.oracle.artifacts import read_artifact


def contains_state(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains_state(actual[key], value)
            for key, value in expected.items()
        )
    return bool(actual == expected)


class WorkloadUpdate(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    producer: Literal["workload"]
    state: Object = Field(min_length=1)
    response: Object

    def matches(self, value: Object | None) -> bool:
        if value is None:
            return False
        metadata = value.get("metadata", {})
        accepted = self.response["metadata"]
        return (
            isinstance(metadata, dict)
            and metadata.get("uid") == accepted["uid"]
            and metadata.get("resourceVersion") == accepted["resourceVersion"]
            and contains_state(value, self.state)
        )

    @model_validator(mode="after")
    def accepted_state(self) -> Self:
        metadata = self.response.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("update response must have object metadata")
        if not all(
            isinstance(metadata.get(k), str) and metadata[k]
            for k in ("uid", "resourceVersion")
        ):
            raise ValueError("update response must identify UID and resourceVersion")
        if not contains_state(self.response, self.state):
            raise ValueError("update response does not contain the declared state")
        return self


class IndependentUpdates(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[1]
    status: Literal["completed"]
    workload: str = Field(min_length=1)
    resource_key: str = Field(min_length=1)
    updates: list[WorkloadUpdate] = Field(min_length=3)

    @model_validator(mode="after")
    def one_object(self) -> Self:
        identities, versions = set(), set()
        states = set()
        for update in self.updates:
            obj = update.response
            metadata = obj["metadata"]
            kind = obj.get("kind")
            if not isinstance(kind, str):
                raise ValueError("update response must identify its kind")
            key = "/".join(
                (
                    kind.lower(),
                    metadata.get("namespace", ""),
                    metadata.get("name", ""),
                )
            )
            if key != self.resource_key:
                raise ValueError("update response belongs to a different resource")
            identities.add(metadata["uid"])
            versions.add(metadata["resourceVersion"])
            states.add(json.dumps(update.state, sort_keys=True))
        if (
            len(identities) != 1
            or len(versions) != len(self.updates)
            or len(states) != len(self.updates)
        ):
            raise ValueError(
                "updates must have one identity and distinct versions/states"
            )
        return self


def read_updates(path: str | Path) -> IndependentUpdates:
    return read_artifact(path, TypeAdapter(IndependentUpdates))
