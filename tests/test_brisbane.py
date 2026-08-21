from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import Switch, User
from app.seed import BNE_OFFICE, seed
from app.services.office import build_office_views
from app.services.switch_service import visible_switches


def test_brisbane_switch_counts(seeded_db):
    bne = list(seeded_db.scalars(select(Switch).where(Switch.location == BNE_OFFICE)).all())
    assert len(bne) == 20
    by_stack = {}
    for switch in bne:
        by_stack.setdefault(switch.stack_name, []).append(switch)
    assert len(by_stack["Level 27 Floor Stack"]) == 7
    assert len(by_stack["Level 26 Floor Stack"]) == 5
    assert len(by_stack["Level 21 Floor Stack"]) == 3
    assert len(by_stack["Level 27 Aux Stack"]) == 3
    assert len(by_stack["Level 27 Core Stack"]) == 2
    floors = [s for s in bne if s.stack_role == "floor"]
    aux = [s for s in bne if s.stack_role == "aux"]
    cores = [s for s in bne if s.stack_role == "core"]
    assert len(floors) + len(aux) + len(cores) == 20
    assert all(s.chassis_model == "9500" for s in cores)
    assert all(s.chassis_model == "9300" for s in floors + aux)
    assert all(s.driver_override == "simulator" for s in bne)
    assert all(s.management_ip.startswith("192.0.2.") for s in bne)


def test_brisbane_aux_rack_order_is_3_then_1_then_2(seeded_db):
    aux = list(
        seeded_db.scalars(
            select(Switch).where(Switch.stack_role == "aux").order_by(Switch.rack_order)
        ).all()
    )
    assert [s.name for s in aux] == ["BNE-L27-AUX-03", "BNE-L27-AUX-01", "BNE-L27-AUX-02"]
    assert [s.member_number for s in aux] == [3, 1, 2]
    assert [s.rack_order for s in aux] == [1, 2, 3]


def test_cs_sees_brisbane_switches(seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    names = {s.name for s in visible_switches(seeded_db, cs)}
    assert "BNE-L27-FS-01" in names
    assert "BNE-L27-FS-07" in names
    assert "BNE-L26-FS-05" in names
    assert "BNE-L21-FS-03" in names
    assert "BNE-L27-AUX-03" in names
    assert "BNE-L27-CORE-02" in names
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    assert {s.name for s in visible_switches(seeded_db, nets)} >= names


def test_brisbane_seed_idempotent_does_not_grow(db):
    seed(db)
    first = db.scalar(select(func.count(Switch.id)).where(Switch.location == BNE_OFFICE))
    seed(db)
    second = db.scalar(select(func.count(Switch.id)).where(Switch.location == BNE_OFFICE))
    assert first == 20
    assert second == 20


@pytest.mark.parametrize("username,password", [("cs", "cs"), ("networks", "networks")])
def test_home_and_brisbane_office_list_floor_switch(client, seeded_db, username, password):
    login = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert login.status_code == 303
    home = client.get("/")
    assert home.status_code == 200
    assert "BNE-L27-FS-01" in home.text
    office = client.get("/offices/brisbane")
    assert office.status_code == 200
    assert "BNE-L27-FS-01" in office.text


def test_brisbane_office_layout_not_a_flat_list(client, seeded_db):
    login = client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    assert login.status_code == 303
    home = client.get("/")
    assert home.status_code == 200
    assert "Brisbane" in home.text
    assert "Level 27 Floor Stack" in home.text
    assert "Level 26 Floor Stack" in home.text
    assert "Level 21 Floor Stack" in home.text
    assert "Level 27 Main Comms Room" in home.text
    assert 'class="rack' in home.text
    assert "BNE-L27-AUX-03" in home.text
    aux_pos = [home.text.find(name) for name in ("BNE-L27-AUX-03", "BNE-L27-AUX-01", "BNE-L27-AUX-02")]
    assert all(p > 0 for p in aux_pos)
    assert aux_pos == sorted(aux_pos)

    office = client.get("/offices/brisbane")
    assert office.status_code == 200
    assert "mcr-cols" in office.text
    assert "floor-stacks" in office.text
    assert "C9500" in office.text
    assert "C9300-48" in office.text
    assert office.text.count("Level 27 Floor Stack") == 1
    assert "Aux and core" in office.text
    assert office.text.find("Floor stacks") < office.text.find("Aux and core")


def test_brisbane_faceplates_keep_48_port_layout(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    access = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    core = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-CORE-01"))
    lab = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01"))
    access_page = client.get(f"/switches/{access.id}")
    core_page = client.get(f"/switches/{core.id}")
    lab_page = client.get(f"/switches/{lab.id}")
    assert access_page.status_code == 200
    assert "C9300-48" in access_page.text
    assert "chassis-c9500" not in access_page.text
    assert core_page.status_code == 200
    assert "C9500" in core_page.text
    assert "chassis-c9500" in core_page.text
    assert "QSFP" in core_page.text
    assert lab_page.status_code == 200
    assert "C9300-48" in lab_page.text
    assert "chassis-c9500" not in lab_page.text


def test_office_view_aux_order_and_mcr_columns(seeded_db):
    switches = list(seeded_db.scalars(select(Switch).where(Switch.location == BNE_OFFICE)).all())
    views = build_office_views(switches)
    assert len(views) == 1
    office = views[0]
    assert office.slug == "brisbane"
    assert [st.name for st in office.floor_stacks] == [
        "Level 27 Floor Stack",
        "Level 26 Floor Stack",
        "Level 21 Floor Stack",
    ]
    assert [len(st.members) for st in office.floor_stacks] == [7, 5, 3]
    assert office.mcr is not None
    assert [st.role for st in office.mcr.stacks] == ["aux", "core"]
    aux = next(st for st in office.mcr.stacks if st.role == "aux")
    assert [m.name for m in aux.members] == ["BNE-L27-AUX-03", "BNE-L27-AUX-01", "BNE-L27-AUX-02"]
    core = next(st for st in office.mcr.stacks if st.role == "core")
    assert [m.name for m in core.members] == ["BNE-L27-CORE-01", "BNE-L27-CORE-02"]
    assert office.other_rooms == [] or all(not room.has_non_floor for room in office.other_rooms)


def test_networks_permissions_page_groups_brisbane(client, seeded_db):
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get("/admin/permissions")
    assert page.status_code == 200
    assert "Brisbane" in page.text
    assert "BNE-L27-FS-01" in page.text
    assert "Granted" in page.text
