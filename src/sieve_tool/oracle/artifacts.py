"""Typed boundaries for the existing saved oracle formats."""

import json
from pathlib import Path
from typing import Any, Literal, TypeAlias, TypedDict

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, with_config

from sieve_tool.events import Mask, Object

Ignored: TypeAlias = Literal["SIEVE-IGNORE"]
State: TypeAlias = dict[str, Object | Ignored]
Digest: TypeAlias = dict[str, dict[str, int | Ignored] | Ignored]
ControllerFamily: TypeAlias = list[str]


@with_config(ConfigDict(strict=True, extra="forbid"))
class HistoryEntry(TypedDict):
    number: int
    etype: str
    key: str
    state: str


History: TypeAlias = list[HistoryEntry]


class WorkloadOutcome(BaseModel):
    # Process records carry additional command/timing diagnostics. The oracle
    # consults only these fields, including their historical absent/null cases.
    model_config = ConfigDict(strict=True, extra="ignore")
    status: str | None = None
    returncode: int | None = None


class ArtifactError(ValueError):
    """Invalid input evidence, rather than an operator alarm."""


_STATE: TypeAdapter[State] = TypeAdapter(State, config=ConfigDict(strict=True))
_DIGEST: TypeAdapter[Digest] = TypeAdapter(Digest, config=ConfigDict(strict=True))
_FAMILY: TypeAdapter[ControllerFamily] = TypeAdapter(
    ControllerFamily, config=ConfigDict(strict=True)
)
_HISTORY: TypeAdapter[History] = TypeAdapter(History)
_MASK: TypeAdapter[Mask] = TypeAdapter(
    dict[str, list[list[str]]], config=ConfigDict(strict=True)
)
_OUTCOME: TypeAdapter[WorkloadOutcome] = TypeAdapter(WorkloadOutcome)


def _unique_object(pairs: list[tuple[str, Any]]) -> Object:
    result: Object = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value}")


def read_artifact[T](path: str | Path, adapter: TypeAdapter[T]) -> T:
    source = Path(path)
    try:
        with source.open() as stream:
            data = json.load(
                stream,
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        return adapter.validate_python(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, item['loc'])) or '<root>'}: {item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise ArtifactError(f"{source}: {problems}") from None
    except (ValueError, OSError) as error:
        raise ArtifactError(f"{source}: {error}") from error


def read_state(path: str | Path) -> State:
    return read_artifact(path, _STATE)


def read_digest(path: str | Path) -> Digest:
    return read_artifact(path, _DIGEST)


def read_family(path: str | Path) -> ControllerFamily:
    return read_artifact(path, _FAMILY)


def read_history(path: str | Path) -> History:
    return read_artifact(path, _HISTORY)


def read_mask(path: str | Path) -> Mask:
    return read_artifact(path, _MASK)


def read_outcome(path: str | Path) -> WorkloadOutcome:
    return read_artifact(path, _OUTCOME)
