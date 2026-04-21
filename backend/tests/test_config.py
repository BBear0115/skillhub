from pathlib import Path

import os

from app.config import BACKEND_DIR, _resolve_sqlite_url, _resolve_storage_root


def test_resolve_relative_sqlite_url_against_backend_dir() -> None:
    resolved = _resolve_sqlite_url("sqlite:///./data/skillhub.db")
    expected = f"sqlite:///{(BACKEND_DIR / 'data' / 'skillhub.db').resolve().as_posix()}"
    assert resolved == expected


def test_resolve_relative_storage_root_against_backend_dir() -> None:
    resolved = _resolve_storage_root("./storage", "sqlite:///./data/skillhub.db")
    assert resolved == str((BACKEND_DIR / "storage").resolve())


def test_keep_absolute_storage_root() -> None:
    absolute = str((Path.cwd() / "tmp-storage").resolve())
    assert _resolve_storage_root(absolute, "sqlite:///./data/skillhub.db") == absolute


def test_namespace_storage_root_for_in_memory_sqlite() -> None:
    resolved = _resolve_storage_root("./storage", "sqlite://")
    assert resolved == str((BACKEND_DIR / "storage" / f"runtime-{os.getpid()}").resolve())
