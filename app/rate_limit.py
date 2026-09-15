from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select

from app.config import get_settings
from app.models import LoginFailure, utcnow

_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES = 8
_failures: dict[str, list[float]] = defaultdict(list)


def _prune_memory(now: float, stamps: list[float]) -> list[float]:
    cutoff = now - _WINDOW_SECONDS
    return [stamp for stamp in stamps if stamp >= cutoff]


def client_ip(request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded and get_settings().trust_x_forwarded_for:
        return forwarded
    return request.client.host if request.client else "unknown"


def _count_persisted(ip: str) -> int | None:
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            cutoff = utcnow() - timedelta(seconds=_WINDOW_SECONDS)
            db.execute(delete(LoginFailure).where(LoginFailure.created_at < cutoff))
            rows = db.scalars(
                select(LoginFailure).where(LoginFailure.ip == ip, LoginFailure.created_at >= cutoff)
            ).all()
            db.commit()
            return len(rows)
        finally:
            db.close()
    except Exception:
        return None


def _record_persisted(ip: str) -> bool:
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            db.add(LoginFailure(ip=ip[:64]))
            db.commit()
            return True
        finally:
            db.close()
    except Exception:
        return False


def _clear_persisted(ip: str | None = None) -> None:
    try:
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            stmt = delete(LoginFailure)
            if ip is not None:
                stmt = stmt.where(LoginFailure.ip == ip)
            db.execute(stmt)
            db.commit()
        finally:
            db.close()
    except Exception:
        return


def login_is_blocked(ip: str) -> bool:
    if not get_settings().login_rate_limit:
        return False
    persisted = _count_persisted(ip)
    if persisted is not None:
        return persisted >= _MAX_FAILURES
    import time

    now = time.monotonic()
    stamps = _prune_memory(now, _failures.get(ip, []))
    _failures[ip] = stamps
    return len(stamps) >= _MAX_FAILURES


def record_login_failure(ip: str) -> None:
    if not get_settings().login_rate_limit:
        return
    if _record_persisted(ip):
        return
    import time

    now = time.monotonic()
    _failures[ip] = _prune_memory(now, _failures.get(ip, [])) + [now]


def clear_login_failures(ip: str) -> None:
    _failures.pop(ip, None)
    _clear_persisted(ip)


def reset_login_failures_for_tests() -> None:
    _failures.clear()
    _clear_persisted()
