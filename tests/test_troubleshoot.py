from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import TroubleshootingSession, User, utcnow
from app.services.switch_service import (
    TroubleshootConflict,
    start_troubleshooting,
    tick_troubleshooting,
)
from tests.conftest import first_port


def test_session_auto_stops_after_end_time(seeded_db):
    user = seeded_db.scalar(select(User).where(User.username == "cs"))
    port = first_port(seeded_db)
    session = start_troubleshooting(seeded_db, user, port)
    session.ends_at = utcnow() - timedelta(seconds=1)
    seeded_db.commit()

    tick_troubleshooting(seeded_db)
    seeded_db.commit()

    again = seeded_db.get(TroubleshootingSession, session.id)
    assert again.is_active is False
    assert again.stopped_at is not None


def test_one_active_session_per_user(seeded_db):
    user = seeded_db.scalar(select(User).where(User.username == "cs"))
    port = first_port(seeded_db)
    start_troubleshooting(seeded_db, user, port)
    seeded_db.commit()
    from app.models import Port

    other = seeded_db.scalar(select(Port).where(Port.switch_id == port.switch_id, Port.id != port.id))
    try:
        start_troubleshooting(seeded_db, user, other)
        raise AssertionError("second session should be blocked")
    except TroubleshootConflict:
        pass
