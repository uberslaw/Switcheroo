from __future__ import annotations

from sqlalchemy import func, select

from app.models import Port, Switch, User
from app.seed import BNE_OFFICE, SWITCH_SPECS, seed
from app.services.office import build_office_views
from app.services.switch_service import visible_switches

BNE_NAMES = {spec["name"] for spec in SWITCH_SPECS if spec.get("location") == BNE_OFFICE}
LAB_NAMES = {"CS-BLD-A-AS01", "CS-BLD-B-AS01"}


def test_seed_is_idempotent(db):
    first = seed(db)
    assert first["users"] == 2
    assert first["switches"] == 22
    users = db.scalar(select(func.count(User.id)))
    switches = db.scalar(select(func.count(Switch.id)))
    ports = db.scalar(select(func.count(Port.id)))
    assert users == 2
    assert switches == 22
    assert ports == 22 * 48

    second = seed(db)
    assert second["users"] == 0
    assert second["switches"] == 0
    assert db.scalar(select(func.count(User.id))) == 2
    assert db.scalar(select(func.count(Switch.id))) == 22
    assert db.scalar(select(func.count(Port.id))) == 22 * 48
    names = set(db.scalars(select(Switch.name)).all())
    assert names == LAB_NAMES | BNE_NAMES


def test_seed_upserts_layout_on_existing_brisbane_row(db):
    stub = Switch(
        name="BNE-L27-FS-01",
        management_ip="192.0.2.21",
        location="old-office",
        notes="pre-layout row",
        driver_override="simulator",
    )
    db.add(stub)
    db.commit()
    seed(db)
    switch = db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    assert switch.location == BNE_OFFICE
    assert switch.stack_name == "Level 27 Floor Stack"
    assert switch.stack_role == "floor"
    assert switch.member_number == 1
    assert switch.rack_order == 1
    assert db.scalar(select(func.count(Port.id)).where(Port.switch_id == switch.id)) == 48
    views = build_office_views([switch])
    assert views[0].slug == "brisbane"
