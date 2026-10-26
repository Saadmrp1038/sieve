"""Identify the checked-out source used by CI and acceptance runs."""

import re
from pathlib import Path
from typing import TypedDict

from sieve_tool.process import run


class SourceRevision(TypedDict):
    revision: str
    tree: str


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
