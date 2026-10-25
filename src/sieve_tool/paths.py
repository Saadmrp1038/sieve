"""Repository assets are explicit; importing the package never changes cwd."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

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
def repository(root: str | Path) -> Iterator[Path]:
    """Select repository assets without changing the process working directory."""
    root = Path(root).resolve()
    token = _root.set(root)
    try:
        yield root
    finally:
        _root.reset(token)


def asset_path(path: str | Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else repository_root() / path
