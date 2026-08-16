from __future__ import annotations

from datetime import timedelta

from app.models import utcnow
from app.services.cooldown import CooldownActive, assert_can_refresh, can_refresh, remaining_seconds
from app.services.switch_service import refresh_port
from tests.conftest import first_port


def test_remaining_seconds_zero_when_never_refreshed():
    assert remaining_seconds(None) == 0


def test_remaining_seconds_counts_down():
    now = utcnow()
    last = now - timedelta(seconds=20)
    left = remaining_seconds(last, now=now)
    assert 39 <= left <= 40


def test_shared_cooldown_blocks_second_refresh(seeded_db):
    port = first_port(seeded_db)
    refresh_port(seeded_db, port, honor_cooldown=True)
    seeded_db.commit()
    ok, left = can_refresh(port)
    assert ok is False
    assert left > 0
    try:
        refresh_port(seeded_db, port, honor_cooldown=True)
        raise AssertionError("second refresh should be blocked")
    except CooldownActive as exc:
        assert exc.remaining > 0


def test_assert_can_refresh_raises(seeded_db):
    port = first_port(seeded_db)
    port.last_on_demand_at = utcnow()
    try:
        assert_can_refresh(port)
        raise AssertionError("expected cooldown")
    except CooldownActive:
        pass
