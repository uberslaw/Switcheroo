from __future__ import annotations

from sqlalchemy import func, select

from app.models import ChangeRequest, Port, Switch, User
from app.poller import poll_all_status
from app.services.switch_service import monitored_switches, visible_switches
from tests.conftest import first_port


def test_help_link_sits_next_to_sign_out(cs_client, seeded_db):
    page = cs_client.get("/")
    assert page.status_code == 200
    assert 'href="/help"' in page.text
    assert ">Help</a>" in page.text


def test_pause_monitoring_stops_poller(networks_client, seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))
    assert switch.monitoring_enabled
    response = networks_client.post(
        f"/admin/switches/{switch.id}/monitoring",
        data={"enabled": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    seeded_db.expire_all()
    seeded_db.refresh(switch)
    assert not switch.monitoring_enabled
    assert switch.id not in {s.id for s in monitored_switches(seeded_db)}

    switch.last_status_poll_at = None
    seeded_db.commit()
    poll_all_status()
    seeded_db.expire_all()
    seeded_db.refresh(switch)
    assert switch.last_status_poll_at is None

    other = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    assert other.last_status_poll_at is not None


def test_paused_switch_hidden_from_cs(seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))
    switch.monitoring_enabled = False
    seeded_db.commit()
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    names = [s.name for s in visible_switches(seeded_db, cs)]
    assert "CS-BLD-A-AS01" not in names
    assert "CS-BLD-B-AS01" in names


def test_cs_cannot_pause_monitoring(cs_client, seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))
    response = cs_client.post(
        f"/admin/switches/{switch.id}/monitoring",
        data={"enabled": "0"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    seeded_db.refresh(switch)
    assert switch.monitoring_enabled


def test_delete_requires_typed_name(networks_client, seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))
    switch_id = switch.id
    denied = networks_client.post(
        f"/admin/switches/{switch_id}/delete",
        data={"confirm_name": "wrong"},
        follow_redirects=False,
    )
    assert denied.status_code == 303
    assert seeded_db.get(Switch, switch_id) is not None

    ok = networks_client.post(
        f"/admin/switches/{switch_id}/delete",
        data={"confirm_name": "CS-BLD-A-AS01"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    seeded_db.expire_all()
    assert seeded_db.get(Switch, switch_id) is None
    assert seeded_db.scalar(select(func.count(Port.id)).where(Port.switch_id == switch_id)) == 0
    assert seeded_db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.switch_id == switch_id)) == 0


def test_paused_switch_blocks_refresh(networks_client, seeded_db):
    port = first_port(seeded_db)
    switch = seeded_db.get(Switch, port.switch_id)
    switch.monitoring_enabled = False
    seeded_db.commit()
    response = networks_client.post(
        f"/switches/{switch.id}/ports/{port.id}/refresh",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Monitoring is paused" in response.text
