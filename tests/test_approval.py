from __future__ import annotations

from sqlalchemy import select

from app.drivers.simulator import simulator
from app.models import REQUEST_BOUNCE, REQUEST_VLAN, STATUS_EXECUTED, STATUS_REJECTED, User
from app.services.request_service import approve_request, create_request, reject_request
from tests.conftest import first_port


def test_approve_vlan_updates_simulator(seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    port = first_port(seeded_db)
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    approve_request(seeded_db, req, nets, "ok")
    seeded_db.commit()

    seeded_db.refresh(port)
    assert req.status == STATUS_EXECUTED
    assert port.vlan_id == 50
    assert port.vlan_name == "GUEST"
    sim = simulator.get_port(port.switch, port.if_name)
    assert sim.vlan_id == 50
    assert sim.vlan_changes >= 1


def test_approve_bounce_increments_simulator(seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    port = first_port(seeded_db)
    before = simulator.get_port(port.switch, port.if_name).bounce_count
    req = create_request(seeded_db, cs, port, REQUEST_BOUNCE)
    seeded_db.flush()
    approve_request(seeded_db, req, nets)
    seeded_db.commit()
    assert req.status == STATUS_EXECUTED
    assert simulator.get_port(port.switch, port.if_name).bounce_count == before + 1


def test_reject_requires_note(seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    port = first_port(seeded_db)
    req = create_request(seeded_db, cs, port, REQUEST_BOUNCE)
    seeded_db.flush()
    try:
        reject_request(seeded_db, req, nets, "")
        raise AssertionError("empty note should fail")
    except Exception as exc:
        assert "note" in str(exc).lower()
    reject_request(seeded_db, req, nets, "desk still in use")
    seeded_db.commit()
    assert req.status == STATUS_REJECTED
    assert req.review_note == "desk still in use"
