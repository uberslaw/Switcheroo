from __future__ import annotations

from sqlalchemy import select

from app.drivers.simulator import simulator
from app.models import Port, Switch
from app.services.switch_service import poll_switch_status
from tests.conftest import first_port


def test_status_poll_updates_oper_status(seeded_db):
    port = first_port(seeded_db)
    switch = seeded_db.get(Switch, port.switch_id)
    sim = simulator.get_port(switch, port.if_name)
    sim.admin_status = "up"
    sim.oper_status = "down"
    port.oper_status = "up"
    seeded_db.commit()

    poll_switch_status(seeded_db, switch)
    seeded_db.commit()

    seeded_db.refresh(port)
    assert port.oper_status == "down"
    assert port.last_status_poll_at is not None
    assert switch.last_status_poll_at is not None
    assert switch.next_status_poll_at is not None
    assert switch.last_poll_error is None


def test_poll_failure_is_recorded_not_raised(seeded_db, monkeypatch):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))

    class Boom:
        def poll_interface_status(self, _switch, _names):
            raise RuntimeError("box unreachable")

    monkeypatch.setattr("app.services.switch_service.get_driver", lambda _switch: Boom())
    poll_switch_status(seeded_db, switch)
    seeded_db.commit()
    seeded_db.refresh(switch)
    assert switch.last_poll_error is not None
    assert "unreachable" in switch.last_poll_error
    port = seeded_db.scalar(select(Port).where(Port.switch_id == switch.id))
    assert port.last_poll_error is not None
