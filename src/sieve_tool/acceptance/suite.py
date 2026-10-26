"""Frozen schedules and the source inputs needed to reuse instrumented images."""

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from sieve_tool.oracle.artifacts import read_artifact
from sieve_tool.plans import load_plan
from sieve_tool.process import run

Hex40 = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Hex64 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageID = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
type Selection = Literal["smoke", "full"]
MODES = ("vanilla", "learn", "test")


class Case(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    name: Annotated[str, Field(pattern=r"^[a-z0-9-]+$")]
    family: Literal["intermediate-state", "unobserved-state"]
    sha256: Hex64
    smoke: bool

    @property
    def relative_path(self) -> Path:
        return Path(self.family) / (self.name + ".yaml")


class Suite(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal[1]
    controller: Literal["kubernetes-secret-generator"]
    workload: Literal["create-update-recreate"]
    controller_revision: Hex40
    kubernetes_version: Literal["v1.24.10"]
    cases: list[Case]

    def select(self, selection: Selection) -> list[Case]:
        cases = [case for case in self.cases if selection == "full" or case.smoke]
        return sorted(
            cases,
            key=lambda case: (
                case.name
                not in {"unobserved-state-test-plan-1", "unobserved-state-test-plan-7"},
                case.family,
                int(case.name.rsplit("-", 1)[1]),
            ),
        )


class ImageManifest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: Literal[1] = 1
    registry: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.:/_-]*$")]
    built_revision: Hex40
    build_inputs: dict[str, Hex64]
    images: dict[str, ImageID]


SUITE_ADAPTER = TypeAdapter(Suite)
IMAGE_ADAPTER = TypeAdapter(ImageManifest)


def suite_directory(root: Path) -> Path:
    return root / "fixtures/acceptance/secret-generator"


def load_suite(root: Path) -> Suite:
    directory = suite_directory(root)
    suite = read_artifact(directory / "suite.json", SUITE_ADAPTER)
    if len(suite.cases) != 12 or len({c.name for c in suite.cases}) != 12:
        raise ValueError("Acceptance suite must contain twelve distinct frozen plans")
    for selection in ("smoke", "full"):
        cases = suite.select(selection)
        expected = (1, 1) if selection == "smoke" else (5, 7)
        actual = tuple(
            sum(c.family == family for c in cases)
            for family in ("intermediate-state", "unobserved-state")
        )
        if actual != expected:
            raise ValueError(f"Unexpected {selection} acceptance case counts: {actual}")
    for case in suite.cases:
        path = directory / case.relative_path
        if file_hash(path) != case.sha256:
            raise ValueError(f"Frozen acceptance plan changed: {path}")
        if load_plan(path).workload != suite.workload:
            raise ValueError(f"Unexpected workload in {path}")
    return suite


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_references(suite: Suite, registry: str) -> list[str]:
    return [
        reference
        for mode in MODES
        for reference in (
            f"{registry}/node:{suite.kubernetes_version}-{mode}",
            f"{registry}/{suite.controller}:{mode}",
        )
    ]


def build_inputs(root: Path) -> dict[str, str]:
    # Ignore generated caches; track source bytes and executable bits for the
    # hooks, AST rewrites, image build rules and the pinned controller profile.
    paths = [
        "go/client",
        "go/instrumenter",
        "go.work",
        "go.work.sum",
        "controllers/kubernetes-secret-generator",
        "src/sieve_tool/build",
        "src/sieve_tool/docker_proxy.py",
        "src/sieve_tool/common/config.py",
        "src/sieve_tool/resources/defaults.json",
        "uv.lock",
    ]
    listed = run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *paths],
        operation="enumerate image build inputs",
        capture_output=True,
    ).stdout
    result = {}
    for name in filter(None, listed.split("\0")):
        path = root / name
        content = (
            path.readlink().as_posix().encode()
            if path.is_symlink()
            else path.read_bytes()
        )
        mode = "link" if path.is_symlink() else str(path.stat().st_mode & 0o111)
        result[name] = hashlib.sha256(mode.encode() + b"\0" + content).hexdigest()
    if not result:
        raise ValueError("No image build inputs found")
    return result


def save_json(path: Path, value: object) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)
