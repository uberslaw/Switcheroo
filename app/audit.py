from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.filesec import restrict_private_file

log = logging.getLogger("switcheroo.audit")

_REDACT_PARTS = ("password", "secret", "token", "webhook", "cookie", "authorization", "sig")
AUDIT_MAX_BYTES = 2_000_000
AUDIT_BACKUPS = 5
AUDIT_RETENTION_DAYS = 90
AUDIT_NAME = "audit.log"


def audit_path() -> Path:
    return get_settings().data_dir / AUDIT_NAME


def _rotate_if_needed(path: Path) -> None:
    try:
        if not path.is_file() or path.stat().st_size < AUDIT_MAX_BYTES:
            return
    except OSError:
        return
    oldest = path.with_name(f"{path.name}.{AUDIT_BACKUPS}")
    try:
        if oldest.exists():
            oldest.unlink()
    except OSError:
        pass
    for index in range(AUDIT_BACKUPS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dest = path.with_name(f"{path.name}.{index + 1}")
        if src.exists():
            try:
                src.replace(dest)
            except OSError:
                pass
    try:
        path.replace(path.with_name(f"{path.name}.1"))
    except OSError:
        return


def audit(action: str, **fields: Any) -> None:
    """Append one JSON line to data/audit.log. Never persist secrets."""
    safe: dict[str, Any] = {"ts": datetime.now(timezone.utc).isoformat(), "action": action}
    for key, value in fields.items():
        lower = key.lower()
        if any(part in lower for part in _REDACT_PARTS):
            continue
        if isinstance(value, str) and len(value) > 256:
            value = value[:256]
        safe[key] = value
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = audit_path()
    _rotate_if_needed(path)
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, default=str) + "\n")
        restrict_private_file(path)
    except OSError as exc:
        log.warning("Could not write audit log: %s", exc)


def read_audit_events(limit: int = 200, action: str | None = "") -> list[dict[str, Any]]:
    """Newest-first events from audit.log and rotated siblings."""
    settings = get_settings()
    paths = [audit_path()]
    for index in range(1, AUDIT_BACKUPS + 1):
        paths.append(settings.data_dir / f"{AUDIT_NAME}.{index}")
    needle = (action or "").strip()
    events: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            if needle and str(item.get("action") or "") != needle:
                continue
            events.append(item)
            if len(events) >= limit:
                return events
    return events
