from __future__ import annotations

from sqlalchemy import func, select

from app.models import CablePath, FieldOutlet, Patch, PatchPanel, Port, Switch
from app.seed import BNE_OFFICE, seed
from app.services.patching import available_ports, is_available_switch_port, patched_switch_port_ids


def test_brisbane_field_outlets_unique_mix_of_patched(seeded_db):
    codes = list(seeded_db.scalars(select(FieldOutlet.code).order_by(FieldOutlet.code)).all())
    assert len(codes) == len(set(codes))
    assert "FO-27001" in codes
    assert "FO-27013" in codes
    assert "FO-26001" in codes
    assert "FO-21001" in codes
    patched = seeded_db.scalar(select(func.count(Patch.id)))
    outlets = seeded_db.scalar(select(func.count(FieldOutlet.id)))
    assert patched and patched < outlets
    fo1 = seeded_db.scalar(select(FieldOutlet).where(FieldOutlet.code == "FO-27001"))
    fo13 = seeded_db.scalar(select(FieldOutlet).where(FieldOutlet.code == "FO-27013"))
    assert fo1 is not None and fo1.patch is not None
    assert fo13 is not None and fo13.patch is None


def test_patching_seed_idempotent(db):
    seed(db)
    first_fo = db.scalar(select(func.count(FieldOutlet.id)))
    first_patch = db.scalar(select(func.count(Patch.id)))
    seed(db)
    assert db.scalar(select(func.count(FieldOutlet.id))) == first_fo
    assert db.scalar(select(func.count(Patch.id))) == first_patch


def test_stacks_view_still_single_l27_floor_stack(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    office = client.get("/offices/brisbane")
    assert office.status_code == 200
    assert office.text.count("Level 27 Floor Stack") == 1
    assert "Show patching" in office.text
    assert "BNE-L27-PP-01" not in office.text


def test_patched_fo_pane_has_network_fields(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/offices/brisbane?view=patching&fo=FO-27001")
    assert page.status_code == 200
    assert "FO-27001" in page.text
    assert "Patched to FO-" in page.text
    assert "<dt>VLAN</dt>" in page.text
    assert "<dt>MAC</dt>" in page.text
    assert "BNE-L27-FS-01" in page.text
    assert "Gi1/0/1" in page.text or "GigabitEthernet1/0/1" in page.text


def test_unpatched_fo_pane_has_available_not_live_mac(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/offices/brisbane?view=patching&fo=FO-27013")
    assert page.status_code == 200
    assert "FO-27013" in page.text
    assert "Show available ports" in page.text
    assert "Unpatched" in page.text
    assert "<dt>MAC</dt>" not in page.text
    assert "Patched to FO-" not in page.text


def test_available_ports_exclude_patched_shutdown_faulty(seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    assert switch is not None
    ports = {p.if_index: p for p in switch.ports}
    assert ports[1].patch is not None
    assert bool(ports[15].faulty)
    assert (ports[39].admin_status or "").lower() == "down"
    assert ports[31].purpose == "uplink"
    available = available_ports(seeded_db, switch)
    indexes = {p.if_index for p in available}
    assert 1 not in indexes
    assert 12 not in indexes
    assert 15 not in indexes
    assert 31 not in indexes
    assert 32 not in indexes
    assert 39 not in indexes
    assert 40 not in indexes
    assert 13 in indexes
    patched_ids = patched_switch_port_ids(seeded_db)
    for port in switch.ports:
        if is_available_switch_port(port, patched_ids):
            assert port.id in {p.id for p in available}


def test_available_port_highlight_on_office_page(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    page = client.get("/offices/brisbane?view=patching&fo=FO-27013&show_available=1")
    assert page.status_code == 200
    assert "port-available" in page.text
    assert "Available ports" in page.text
    assert "Stack to patch to" in page.text
    assert "Select a switch" in page.text
    assert "BNE-L27-FS-01" in page.text
    assert page.text.count("port-available") >= 1
    picker = client.get(
        f"/offices/brisbane?view=patching&fo=FO-27013&show_available=1&switch={switch.id}"
    )
    assert picker.status_code == 200
    assert "picker-cell" in picker.text
    assert "picker-patched" in picker.text


def test_click_switch_port_shows_patching_and_network(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    port = seeded_db.scalar(
        select(Port).where(Port.switch_id == switch.id, Port.if_index == 1)
    )
    page = client.get(f"/offices/brisbane?view=patching&port={port.id}")
    assert page.status_code == 200
    assert "FO-27001" in page.text
    assert "Patched to FO-" in page.text
    assert "<dt>VLAN</dt>" in page.text
    assert "<dt>MAC</dt>" in page.text
    assert "Gi1/0/1" in page.text or "GigabitEthernet1/0/1" in page.text


def test_floor_member_has_above_and_below_24_port_panels(seeded_db):
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-FS-01"))
    panels = list(seeded_db.scalars(select(PatchPanel).where(PatchPanel.switch_id == switch.id)).all())
    assert {p.placement for p in panels} == {"above", "below"}
    assert all(p.port_count == 24 for p in panels)
    above = next(p for p in panels if p.placement == "above")
    below = next(p for p in panels if p.placement == "below")
    assert len(above.ports) == 24
    assert len(below.ports) == 24
    fo1 = seeded_db.scalar(select(FieldOutlet).where(FieldOutlet.code == "FO-27001"))
    assert fo1.patch is not None
    assert fo1.patch.port.if_index == 1
    assert fo1.patch.panel_port.panel_id == above.id
    cords = list(
        seeded_db.scalars(
            select(CablePath).where(
                CablePath.from_kind == "panel_port",
                CablePath.to_kind == "switch_port",
                CablePath.length_m.is_not(None),
            )
        ).all()
    )
    assert cords
    assert all(c.length_m == 0.20 for c in cords)
    l27_members = seeded_db.scalar(
        select(func.count(Switch.id)).where(
            Switch.location == BNE_OFFICE, Switch.stack_name == "Level 27 Floor Stack"
        )
    )
    l27_panels = seeded_db.scalar(
        select(func.count(PatchPanel.id)).where(
            PatchPanel.location == BNE_OFFICE, PatchPanel.name.like("BNE-L27-FS-%")
        )
    )
    assert l27_members == 7
    assert l27_panels == 14


def test_patching_view_draws_adjacent_ru_sandwich(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/offices/brisbane?view=patching")
    assert page.status_code == 200
    assert "BNE-L27-FS-01-PP-ABOVE" in page.text
    assert "BNE-L27-FS-01-PP-BELOW" in page.text
    assert page.text.find("BNE-L27-FS-01-PP-ABOVE") < page.text.find("<strong>BNE-L27-FS-01</strong>")
    assert page.text.find("<strong>BNE-L27-FS-01</strong>") < page.text.find("BNE-L27-FS-01-PP-BELOW")
    assert "C9300-48" in page.text
    assert "rj45" in page.text
    assert "20 cm" in page.text
    assert "BNE-L27-PP-01" not in page.text
    stacks = client.get("/offices/brisbane")
    assert "BNE-L27-FS-01-PP-ABOVE" not in stacks.text


def test_aux_1_and_2_have_fo_panels_switch_3_does_not(seeded_db):
    aux1 = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-AUX-01"))
    aux2 = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-AUX-02"))
    aux3 = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-AUX-03"))
    p1 = list(seeded_db.scalars(select(PatchPanel).where(PatchPanel.switch_id == aux1.id)).all())
    p2 = list(seeded_db.scalars(select(PatchPanel).where(PatchPanel.switch_id == aux2.id)).all())
    p3 = list(seeded_db.scalars(select(PatchPanel).where(PatchPanel.switch_id == aux3.id)).all())
    assert {p.placement for p in p1} == {"above", "below"}
    assert {p.placement for p in p2} == {"above", "below"}
    assert p3 == []
    fo = seeded_db.scalar(select(FieldOutlet).where(FieldOutlet.code == "FO-A1001"))
    assert fo is not None
    assert fo.patch is not None
    assert fo.patch.port.switch_id == aux1.id
    assert fo.patch.port.if_index == 1


def test_third_aux_is_the_empty_switch(seeded_db):
    aux3 = seeded_db.scalar(select(Switch).where(Switch.name == "BNE-L27-AUX-03"))
    assert aux3 is not None
    access = [p for p in aux3.ports if p.purpose != "uplink"]
    assert access
    assert all(p.purpose == "unused" for p in access)
    assert all((p.oper_status or "").lower() == "down" for p in access)
    assert all(p.mac_address is None for p in access)
    assert not any(p.patch for p in aux3.ports)
    assert seeded_db.scalar(select(func.count(PatchPanel.id)).where(PatchPanel.switch_id == aux3.id)) == 0


def test_aux_patching_view_order_and_cable_routing(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/offices/brisbane?view=patching&stack=level-27-aux-stack")
    assert page.status_code == 200
    assert "cable routing" in page.text
    assert "3rd aux" in page.text
    assert "empty" in page.text
    assert "BNE-L27-AUX-01-PP-ABOVE" in page.text
    assert "BNE-L27-AUX-02-PP-BELOW" in page.text
    assert "BNE-L27-AUX-03-PP-ABOVE" not in page.text
    assert page.text.find("BNE-L27-AUX-03") < page.text.find("BNE-L27-AUX-01-PP-ABOVE")
    assert page.text.find("BNE-L27-AUX-01-PP-ABOVE") < page.text.find("<strong>BNE-L27-AUX-01</strong>")
    assert page.text.find("<strong>BNE-L27-AUX-01</strong>") < page.text.find("BNE-L27-AUX-02-PP-ABOVE")
    stacks = client.get("/offices/brisbane")
    assert "cable routing" not in stacks.text
    assert "BNE-L27-AUX-01-PP-ABOVE" not in stacks.text

