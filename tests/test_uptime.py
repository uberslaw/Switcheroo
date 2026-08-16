from __future__ import annotations

from datetime import timedelta

from app.models import Port, utcnow
from app.services.switch_service import apply_status
from app.services.uptime import format_connected_for
from tests.conftest import first_port


def test_format_connected_for_not_connected():
    assert format_connected_for(None) == "Not connected"


def test_format_connected_for_hours_and_minutes():
    now = utcnow()
    since = now - timedelta(hours=3, minutes=12)
    assert format_connected_for(since, now=now) == "Connected for 3h 12m"


def test_format_connected_for_seconds():
    now = utcnow()
    since = now - timedelta(seconds=45)
    assert format_connected_for(since, now=now) == "Connected for 45s"


def test_down_to_up_stamps_link_up_since(seeded_db):
    port = first_port(seeded_db)
    port.oper_status = "down"
    port.admin_status = "up"
    port.link_up_since = None
    before = utcnow()
    apply_status(port, "up", "up")
    seeded_db.commit()
    seeded_db.refresh(port)
    assert port.link_up_since is not None
    assert port.link_up_since >= before - timedelta(seconds=2)


def test_up_to_down_clears_link_up_since(seeded_db):
    port = first_port(seeded_db)
    port.oper_status = "up"
    port.admin_status = "up"
    port.link_up_since = utcnow() - timedelta(hours=2)
    apply_status(port, "down", "up")
    seeded_db.commit()
    seeded_db.refresh(port)
    assert port.link_up_since is None


def test_shutdown_clears_link_up_since(seeded_db):
    port = first_port(seeded_db)
    port.oper_status = "up"
    port.admin_status = "up"
    port.link_up_since = utcnow() - timedelta(minutes=30)
    apply_status(port, "down", "down")
    assert port.link_up_since is None


def test_first_observation_already_up_stamps_now_not_history():
    port = Port(
        switch_id=1,
        if_name="GigabitEthernet1/0/99",
        if_index=99,
        oper_status="unknown",
        admin_status="up",
        link_up_since=None,
    )
    now = utcnow()
    apply_status(port, "up", "up")
    assert port.link_up_since is not None
    assert abs((port.link_up_since - now).total_seconds()) < 2


def test_seed_up_ports_have_lab_uptime(seeded_db):
    port = first_port(seeded_db)
    assert port.oper_status == "up"
    assert port.link_up_since is not None
    assert port.link_up_since < utcnow()
    assert "Connected for" in format_connected_for(port.link_up_since)
