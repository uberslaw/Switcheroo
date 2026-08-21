from __future__ import annotations

from sqlalchemy import select

from app.models import (
    RACK_CAP_EDIT_LAYOUT,
    RACK_CAP_VIEW,
    RACK_FACE_FRONT,
    Rack,
    RackItem,
    RackItemType,
    RackSite,
    UserRackPermission,
)
from app.services import rack_design as rd
from app.services.rack_import import import_brisbane_layout, parse_brisbane_workbook
from tests.conftest import add_cs_user


def test_parse_brisbane_workbook_has_doc_ru_order():
    parsed = parse_brisbane_workbook()
    assert parsed["site"]["name"] == "Brisbane Albert St"
    mcr = parsed["rooms"][0]
    assert mcr["floor"] == "L27"
    assert len(mcr["racks"]) == 4
    server_b = next(r for r in mcr["racks"] if r["name"] == "Server Rack B")
    assert server_b["ru_height"] >= 45
    # spans use top RU first; high numbers at top of sheet
    tops = [span[0] for span in server_b["spans"]]
    assert tops == sorted(tops, reverse=True)
    assert any(span[0] >= 40 for span in server_b["spans"])


def test_seed_imports_racks_and_cs_perms(seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    assert site is not None
    racks = list(seeded_db.scalars(select(Rack).where(Rack.site_id == site.id)).all())
    assert len(racks) >= 6
    items = list(seeded_db.scalars(select(RackItem)).all())
    assert len(items) > 20
    # Doc numbering: some item sits near top (high RU)
    assert any(i.ru_start >= 40 for i in items if i.ru_height)
    cs = seeded_db.scalar(select(UserRackPermission).where(UserRackPermission.capability == RACK_CAP_VIEW))
    assert cs is not None


def test_elevation_rows_top_is_high_ru(seeded_db):
    rack = seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26"))
    assert rack is not None
    elev = rd.elevation_rows(rd.get_rack(seeded_db, rack.id), RACK_FACE_FRONT)
    assert elev["rows"][0]["ru"] == rack.ru_height
    assert elev["rows"][-1]["ru"] == 1
    assert elev["side_left"] or elev["side_right"]


def test_place_and_move_item(seeded_db):
    from fastapi import HTTPException

    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    assert site is not None
    rack = Rack(
        site_id=site.id,
        name="Test empty rack",
        floor="L99",
        room="Lab",
        ru_height=10,
        sort_order=999,
    )
    seeded_db.add(rack)
    seeded_db.flush()
    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    assert blank is not None
    item = rd.place_item(
        seeded_db,
        rack,
        item_type_id=blank.id,
        name="Test blank",
        ru_start=8,
        ru_height=2,
        face=RACK_FACE_FRONT,
    )
    seeded_db.flush()
    assert item.ru_start == 8
    assert item.ru_end == 7
    rd.move_item(seeded_db, item, ru_start=5)
    assert item.ru_start == 5
    assert item.ru_end == 4
    other = rd.place_item(
        seeded_db,
        rack,
        item_type_id=blank.id,
        name="Blocker",
        ru_start=10,
        ru_height=1,
        face=RACK_FACE_FRONT,
    )
    seeded_db.flush()
    try:
        rd.move_item(seeded_db, other, ru_start=5)
        raise AssertionError("expected collision")
    except HTTPException as exc:
        assert "Collides" in str(exc.detail)


def test_cs_can_view_racks(cs_client, seeded_db):
    r = cs_client.get("/racks")
    assert r.status_code == 200
    assert "Rack Design" in r.text
    assert "Brisbane Albert St" in r.text


def test_cs_without_cap_denied(client, seeded_db):
    limited = add_cs_user(seeded_db, "cs-norack", "cs-norack")
    # strip any rack perms seed may not have granted (new user has none)
    for p in list(seeded_db.scalars(select(UserRackPermission).where(UserRackPermission.user_id == limited.id))):
        seeded_db.delete(p)
    seeded_db.commit()
    login = client.post("/login", data={"username": "cs-norack", "password": "cs-norack"}, follow_redirects=False)
    assert login.status_code == 303
    denied = client.get("/racks")
    assert denied.status_code == 403


def test_import_idempotent(seeded_db):
    first = import_brisbane_layout(seeded_db)
    second = import_brisbane_layout(seeded_db)
    assert second["racks"] == 0
    assert second["items"] == 0
    count = len(list(seeded_db.scalars(select(RackItem)).all()))
    assert count > 0
    # force reimport still works
    again = import_brisbane_layout(seeded_db, force=True)
    assert again["items"] > 0


def test_networks_rack_permissions_page(networks_client, seeded_db):
    r = networks_client.get("/admin/rack-permissions")
    assert r.status_code == 200
    assert "Rack Design permissions" in r.text
    assert RACK_CAP_EDIT_LAYOUT in r.text or "Edit physical layout" in r.text
