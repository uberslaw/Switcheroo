from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Iterator

from app.config import get_settings

log = logging.getLogger("switcheroo.step")

FLAG_NAME = "diagnostics.enabled"
DIAG_LOG_NAME = "diagnostics.log"
PID_NAME = "switcheroo.pid"
_REDACT_PARTS = ("password", "secret", "token", "webhook", "cookie", "authorization", "sig")

_diag_handler: RotatingFileHandler | None = None
_last_enabled: bool | None = None


def flag_path() -> Path:
    return get_settings().data_dir / FLAG_NAME


def diagnostics_log_path() -> Path:
    return get_settings().data_dir / DIAG_LOG_NAME


def pid_path() -> Path:
    return get_settings().data_dir / PID_NAME


def write_pid_file(pid: int | None = None) -> Path:
    path = pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid if pid is not None else os.getpid()), encoding="utf-8")
    return path


def clear_pid_file(expected_pid: int | None = None) -> None:
    path = pid_path()
    try:
        text = path.read_text(encoding="utf-8").strip()
        current = str(expected_pid if expected_pid is not None else os.getpid())
        if text == current:
            path.unlink()
    except OSError:
        return


def diagnostics_enabled() -> bool:
    settings = get_settings()
    if settings.diagnostics:
        return True
    try:
        return flag_path().is_file()
    except OSError:
        return False


def set_diagnostics_flag(enabled: bool) -> None:
    path = flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if enabled:
        path.write_text("on\n", encoding="utf-8")
    elif path.exists():
        path.unlink()
    sync_log_level()


def desired_log_level() -> int:
    if diagnostics_enabled():
        return logging.DEBUG
    name = (get_settings().log_level or "INFO").strip().upper()
    if name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        name = "INFO"
    return getattr(logging, name)


def sync_log_level() -> None:
    """Raise or lower loggers when Launch Control flips the diagnostics flag."""
    global _last_enabled
    enabled = diagnostics_enabled()
    level = desired_log_level()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "switcheroo"):
        logging.getLogger(name).setLevel(level)
    _ensure_diag_handler()
    if enabled != _last_enabled:
        _last_enabled = enabled
        logging.getLogger("switcheroo").info(
            "Diagnostics %s (log level %s)",
            "ON" if enabled else "OFF",
            logging.getLevelName(level),
        )


def _ensure_diag_handler() -> None:
    global _diag_handler
    if not diagnostics_enabled():
        return
    path = diagnostics_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _diag_handler is not None and getattr(_diag_handler, "baseFilename", "") == str(path):
        return
    if _diag_handler is not None:
        log.removeHandler(_diag_handler)
        _diag_handler.close()
        _diag_handler = None
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    log.propagate = True
    _diag_handler = handler


def _safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        lower = key.lower()
        if any(part in lower for part in _REDACT_PARTS):
            continue
        if isinstance(value, str) and len(value) > 256:
            value = value[:256]
        safe[key] = value
    return safe


def _field_text(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    if " " in text:
        return text.replace(" ", "_")
    return text


def _emit(name: str, phase: str, elapsed_ms: int, fields: dict[str, Any], error: str | None = None) -> None:
    parts = [name, phase]
    for key, value in _safe_fields(fields).items():
        parts.append(f"{key}={_field_text(value)}")
    parts.append(f"elapsed_ms={elapsed_ms}")
    if error:
        parts.append(f"error={_field_text(error[:512])}")
    line = " ".join(parts)
    if phase == "fail":
        log.error("%s", line)
    elif diagnostics_enabled():
        log.debug("%s", line)
    for handler in log.handlers:
        handler.flush()


@contextmanager
def step(name: str, **fields: Any) -> Iterator[None]:
    """Trace one hop. Failures always log the step name; begin/ok only in diagnostics mode."""
    started = time.perf_counter()
    _emit(name, "begin", 0, fields)
    try:
        yield
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        _emit(name, "fail", elapsed, fields, error=str(exc))
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    _emit(name, "ok", elapsed, fields)
