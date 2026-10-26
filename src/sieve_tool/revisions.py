"""Identify the checked-out source used by CI and acceptance runs."""

import re
from pathlib import Path
from typing import Annotated, TypedDict

from pydantic import Field

from sieve_tool.process import run


class SourceRevision(TypedDict):
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    tree: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


def verify_revision(checkout: Path, expected: str) -> SourceRevision:
    if re.fullmatch(r"[0-9a-f]{40}", expected) is None:
        raise ValueError("Expected revision must be a full lowercase Git commit SHA")
    checkout = checkout.resolve()
    git = ["git", "-C", str(checkout)]
    actual_root = Path(
        run(
            [*git, "rev-parse", "--show-toplevel"],
            operation="locate checkout",
            capture_output=True,
        ).stdout.strip()
    ).resolve()
    if actual_root != checkout:
        raise ValueError(f"Expected a checkout root: {checkout}")
    actual = run(
        [*git, "rev-parse", "HEAD"],
        operation="read source revision",
        capture_output=True,
    ).stdout.strip()
    if actual != expected:
        raise ValueError(f"Checked out {actual}; expected {expected}")
    dirty = run(
        [*git, "status", "--porcelain", "--untracked-files=normal"],
        operation="check source changes",
        capture_output=True,
    ).stdout
    if dirty:
        raise ValueError("Checkout contains source changes; use a clean checkout")
    tree = run(
        [*git, "rev-parse", "HEAD^{tree}"],
        operation="read source tree",
        capture_output=True,
    ).stdout.strip()
    return {"revision": actual, "tree": tree}


def install_integration(
    root: Path, checkout: Path, expected: str, subdirectory: str, controller: str
) -> None:
    """Copy only the selected commit's standalone integration assets."""
    import shutil
    import tarfile
    import tempfile

    from sieve_tool.common.config import load_controller_config

    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", controller) is None:
        raise ValueError("Controller must be a directory name")
    selected = Path(subdirectory)
    if selected.is_absolute() or ".." in selected.parts:
        raise ValueError("Integration subdirectory must stay inside its checkout")
    verify_revision(checkout, expected)
    destination = root / "controllers" / controller
    if (
        destination.is_symlink()
        or not destination.resolve().is_relative_to((root / "controllers").resolve())
        or not (root / "controllers").resolve().is_relative_to(root)
    ):
        raise ValueError("Integration destination escapes the controller directory")
    with tempfile.TemporaryDirectory(prefix="sieve-integration-") as directory:
        temporary = Path(directory)
        archive = temporary / "source.tar"
        run(
            [
                "git",
                "-C",
                str(checkout),
                "archive",
                "--format=tar",
                "-o",
                str(archive),
                expected,
                "--",
                selected.as_posix(),
            ],
            operation="archive verified integration",
        )
        extracted = temporary / "extracted"
        extracted.mkdir()
        try:
            with tarfile.open(archive) as stream:
                stream.extractall(extracted, filter="data")
        except tarfile.TarError as error:
            raise ValueError("Integration archive contains unsafe paths") from error
        profile = extracted / selected
        for entry in profile.rglob("*"):
            if entry.is_symlink() and (
                entry.readlink().is_absolute()
                or not entry.resolve().is_relative_to(profile)
            ):
                raise ValueError(
                    "Integration symlinks must stay within its standalone profile"
                )
        if load_controller_config(profile).controller_name != controller:
            raise ValueError(
                "Integration profile name does not match the requested controller"
            )
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(profile, destination, symlinks=True)
