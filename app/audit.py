from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.filesec import restrict_private_file

log = logging.getLogger("switcheroo.audit")

_REDACT_PARTS = ("password", "secret", "token", "webhook", "cookie", "authorization", "sig")


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
    path = settings.data_dir / "audit.log"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, default=str) + "\n")
        restrict_private_file(path)
    except OSError as exc:
        log.warning("Could not write audit log: %s", exc)
