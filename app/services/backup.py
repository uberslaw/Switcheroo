from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.filesec import restrict_private_dir, restrict_private_file, sqlite_filesystem_path


def backup_dir() -> Path:
    path = get_settings().data_dir / "backups"
    path.mkdir(parents=True, exist_ok=True)
    restrict_private_dir(path)
    return path


def live_sqlite_path() -> Path:
    from app.db import engine

    raw = engine.url.database
    if raw:
        path = Path(raw)
        if path.is_file():
            return path
    path = sqlite_filesystem_path(get_settings().database_url)
    if path is None:
        raise RuntimeError("SWITCHEROO_DATABASE_URL is not a file-backed SQLite database.")
    if not path.is_file():
        raise FileNotFoundError(f"SQLite file does not exist: {path}")
    return path


def sqlite_backup(source: Path, dest: Path) -> Path:
    """Online backup via the SQLite backup API. Do not copy a live file with shutil."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn = sqlite3.connect(str(source))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.execute("PRAGMA wal_checkpoint(FULL)")
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()
    restrict_private_file(dest)
    return dest


def backup_now(label: str | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"-{label}" if label else ""
    dest = backup_dir() / f"switcheroo-{stamp}{suffix}.db"
    return sqlite_backup(live_sqlite_path(), dest)


def restore_from(archive: Path) -> Path:
    live = live_sqlite_path()
    source = Path(archive)
    if not source.is_file():
        raise FileNotFoundError(f"Backup file does not exist: {source}")
    return sqlite_backup(source, live)


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Switcheroo SQLite backup/restore")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("path", nargs="?", help="Backup file for restore")
    args = parser.parse_args()
    try:
        if args.action == "backup":
            print(backup_now())
        else:
            if not args.path:
                raise SystemExit("restore requires a backup file path")
            print(restore_from(Path(args.path)))
    except Exception as exc:  # noqa: BLE001
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
