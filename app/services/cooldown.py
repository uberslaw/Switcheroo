from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.config import get_settings
from app.models import Port, utcnow


class CooldownActive(Exception):
    def __init__(self, remaining: int):
        self.remaining = remaining
        super().__init__(f"On-demand refresh cooldown active: {remaining}s remaining")


def remaining_seconds(last_on_demand_at: Optional[datetime], now: Optional[datetime] = None) -> int:
    settings = get_settings()
    if last_on_demand_at is None:
        return 0
    current = now or utcnow()
    elapsed = (current - last_on_demand_at).total_seconds()
    left = int(settings.on_demand_cooldown - elapsed)
    return max(0, left)


def can_refresh(port: Port, now: Optional[datetime] = None) -> tuple[bool, int]:
    left = remaining_seconds(port.last_on_demand_at, now)
    return left == 0, left


def mark_refreshed(port: Port, now: Optional[datetime] = None) -> None:
    port.last_on_demand_at = now or utcnow()


def assert_can_refresh(port: Port, now: Optional[datetime] = None) -> None:
    ok, left = can_refresh(port, now)
    if not ok:
        raise CooldownActive(left)


def cooldown_deadline(port: Port) -> Optional[datetime]:
    if port.last_on_demand_at is None:
        return None
    return port.last_on_demand_at + timedelta(seconds=get_settings().on_demand_cooldown)
