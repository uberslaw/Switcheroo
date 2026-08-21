from __future__ import annotations

import os
from pathlib import Path


def sqlite_filesystem_path(url: str) -> Path | None:
    """Return the on-disk SQLite path, or None for memory / non-sqlite URLs."""
    if not url.startswith("sqlite"):
        return None
    if url in {"sqlite:///:memory:", "sqlite://"}:
        return None
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if raw == ":memory:" or raw.startswith(":memory:"):
        return None
    return Path(raw)


def restrict_private_dir(path: Path) -> None:
    """Best-effort 0700 on Unix. No-op on filesystems that ignore chmod."""
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def restrict_private_file(path: Path) -> None:
    """Best-effort 0600 so SQLite, logs, and audit files are not world-readable."""
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except OSError:
        pass
