from __future__ import annotations

import time
from collections import defaultdict

from app.config import get_settings

_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES = 8
_failures: dict[str, list[float]] = defaultdict(list)


def _prune(now: float, stamps: list[float]) -> list[float]:
    cutoff = now - _WINDOW_SECONDS
    return [stamp for stamp in stamps if stamp >= cutoff]


def client_ip(request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded and get_settings().trust_x_forwarded_for:
        return forwarded
    return request.client.host if request.client else "unknown"


def login_is_blocked(ip: str) -> bool:
    if not get_settings().login_rate_limit:
        return False
    now = time.monotonic()
    stamps = _prune(now, _failures.get(ip, []))
    _failures[ip] = stamps
    return len(stamps) >= _MAX_FAILURES


def record_login_failure(ip: str) -> None:
    if not get_settings().login_rate_limit:
        return
    now = time.monotonic()
    _failures[ip] = _prune(now, _failures.get(ip, [])) + [now]


def clear_login_failures(ip: str) -> None:
    _failures.pop(ip, None)


def reset_login_failures_for_tests() -> None:
    _failures.clear()
