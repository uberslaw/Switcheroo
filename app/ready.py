from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import __version__
from app.config import Settings, get_settings
from app.filesec import sqlite_filesystem_path

POLLER_HEARTBEAT = "poller.heartbeat"


def heartbeat_path(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return settings.data_dir / POLLER_HEARTBEAT


def write_poller_heartbeat(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = heartbeat_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return path


def _parse_iso(raw: str) -> datetime | None:
    text = raw.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _db_status(settings: Settings) -> dict:
    path = sqlite_filesystem_path(settings.database_url)
    if path is None:
        return {"ok": True, "detail": "non-file database"}
    try:
        conn = sqlite3.connect(str(path))
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": "ok"}


def _poller_status(settings: Settings) -> dict:
    if settings.testing:
        return {"ok": True, "detail": "idle (testing)"}
    path = heartbeat_path(settings)
    if not path.is_file():
        return {"ok": False, "detail": "no heartbeat yet"}
    try:
        stamp = _parse_iso(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}
    if stamp is None:
        return {"ok": False, "detail": "unreadable heartbeat"}
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    limit = max(settings.status_poll_interval * 3, 180)
    if age > limit:
        return {"ok": False, "detail": "stale", "age_seconds": int(age)}
    return {"ok": True, "detail": "ok", "age_seconds": int(age)}


def _integration_mode(*, enabled: bool, dry_run: bool) -> str:
    if not enabled:
        return "off"
    if dry_run:
        return "dry-run"
    return "live"


def build_ready_report(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    db = _db_status(settings)
    poller = _poller_status(settings)
    ok = bool(db["ok"] and poller["ok"])
    return {
        "ok": ok,
        "app": "switcheroo",
        "version": __version__,
        "db": db,
        "poller": poller,
        "servicenow": _integration_mode(enabled=settings.servicenow_enabled, dry_run=settings.servicenow_dry_run),
        "teams": _integration_mode(enabled=settings.teams_enabled, dry_run=settings.teams_dry_run),
    }
