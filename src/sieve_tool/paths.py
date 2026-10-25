"""Repository assets are explicit; importing the package never changes cwd."""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import os


_root: ContextVar[Path | None] = ContextVar("sieve_repository", default=None)


def find_repository(explicit: str | Path | None = None) -> Path:
    """Find a checkout for Go sources, controllers, and research fixtures."""
    if explicit is not None:
        candidates = [Path(explicit).expanduser().resolve()]
    else:
        candidates = [
            Path.cwd(),
            *Path.cwd().parents,
            Path(__file__).resolve().parents[2],
        ]
    for candidate in candidates:
        if (candidate / "go.work").is_file() and (candidate / "controllers").is_dir():
            return candidate
    raise ValueError("Sieve checkout not found; pass --repo /path/to/sieve")


def repository_root() -> Path:
    root = _root.get()
    return root if root is not None else find_repository()


def cache_path(*parts: str) -> Path:
    return repository_root().joinpath(".cache", *parts)


@contextmanager
def repository(root: str | Path):
    """Scope commands to a checkout and always restore the caller's cwd."""
    root = Path(root).resolve()
    token = _root.set(root)
    previous = Path.cwd()
    try:
        os.chdir(root)
        yield root
    finally:
        os.chdir(previous)
        _root.reset(token)
