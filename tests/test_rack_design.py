from __future__ import annotations

from sqlalchemy import select

from app.models import (
    RACK_CAP_EDIT_LAYOUT,
    RACK_CAP_VIEW,
    RACK_FACE_FRONT,
    Rack,
    RackItem,
    RackItemCategory,
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


def test_filler_rows_stay_one_ru_each(seeded_db):
    """A 27U merged blank would force deleting a whole block to place one server."""
    blanks = [
        i
        for i in seeded_db.scalars(select(RackItem)).all()
        if "blanking" in (i.name or "").lower() and i.mount == "ru"
    ]
    assert blanks, "expected imported blanking rows"
    assert max(i.ru_height for i in blanks) == 1
    shelves = [i for i in seeded_db.scalars(select(RackItem)).all() if (i.name or "").lower() == "shelves"]
    assert shelves and max(i.ru_height for i in shelves) == 1


def test_filler_match_does_not_catch_device_names(seeded_db):
    """A substring test made "Netapp Disk Shelf" filler, which would split a
    genuine 2U shelf. Only a leading filler word counts."""
    from app.services.rack_import import _is_filler

    assert _is_filler("Blanking - Spare")
    assert _is_filler("Shelves")
    assert _is_filler("Cable Management")
    assert _is_filler("Reserve for additional shelf expansion")
    assert not _is_filler("Netapp Disk Shelf")
    assert not _is_filler("FAS 2720")
    assert not _is_filler("1.5 KVA UPS (A) (Old) - BNEUPS2601")
    assert not _is_filler("Cisco 9300 sw01 - 48 port POE")


def test_netapp_shelves_keep_their_span(seeded_db):
    netapps = [
        i
        for i in seeded_db.scalars(select(RackItem)).all()
        if "netapp" in (i.name or "").lower() and i.mount == "ru"
    ]
    assert netapps, "expected imported NetApp shelves"
    assert all(i.ru_height >= 2 for i in netapps), "a disk shelf must not collapse to 1U"


def test_real_devices_still_merge(seeded_db):
    """Filler stays 1U, but a genuine multi-U device must keep its span."""
    tall = [
        i
        for i in seeded_db.scalars(select(RackItem)).all()
        if i.mount == "ru" and i.ru_height > 1
    ]
    assert tall, "expected at least one multi-RU device (UPS / NetApp)"
    assert any("ups" in (i.name or "").lower() for i in tall)


def test_reimport_route_rebuilds_site(networks_client, seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    rack = seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26"))
    rd.update_rack(seeded_db, rack, name="Renamed before reimport")
    seeded_db.commit()
    done = networks_client.post(f"/racks/sites/{site.id}/reimport", follow_redirects=False)
    assert done.status_code == 303
    assert seeded_db.scalar(select(Rack).where(Rack.name == "Renamed before reimport")) is None
    assert seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26")) is not None


def test_cs_cannot_reimport(cs_client, seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    denied = cs_client.post(f"/racks/sites/{site.id}/reimport", follow_redirects=False)
    assert denied.status_code == 403


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


def test_create_rack_with_ru_limit(networks_client, seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    made = networks_client.post(
        f"/racks/sites/{site.id}/racks",
        data={"name": "New IDF rack", "floor": "L15", "room": "IDF", "ru_height": "24"},
        follow_redirects=False,
    )
    assert made.status_code == 303
    rack = seeded_db.scalar(select(Rack).where(Rack.name == "New IDF rack"))
    assert rack is not None
    assert rack.ru_height == 24
    elev = rd.elevation_rows(rd.get_rack(seeded_db, rack.id), RACK_FACE_FRONT)
    assert elev["rows"][0]["ru"] == 24
    assert elev["rows"][-1]["ru"] == 1


def test_rename_rack_and_change_ru_limit(seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    rack = rd.create_rack(seeded_db, site_id=site.id, name="Temp rack", ru_height=12)
    seeded_db.flush()
    rd.update_rack(seeded_db, rack, name="Renamed rack", ru_height=20)
    assert rack.name == "Renamed rack"
    assert rack.ru_height == 20


def test_cannot_shrink_rack_below_placed_gear(seeded_db):
    from fastapi import HTTPException

    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    rack = rd.create_rack(seeded_db, site_id=site.id, name="Shrink test", ru_height=20)
    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    rd.place_item(
        seeded_db,
        rack,
        item_type_id=blank.id,
        name="High blank",
        ru_start=18,
        ru_height=1,
        face=RACK_FACE_FRONT,
    )
    seeded_db.flush()
    try:
        rd.update_rack(seeded_db, rack, ru_height=10)
        raise AssertionError("expected refusal")
    except HTTPException as exc:
        assert "RU18" in str(exc.detail)
    assert rack.ru_height == 20


def test_duplicate_rack_name_rejected(seeded_db):
    from fastapi import HTTPException

    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    try:
        rd.create_rack(seeded_db, site_id=site.id, name="FDR L26", ru_height=45)
        raise AssertionError("expected duplicate rejection")
    except HTTPException as exc:
        assert "already has a rack" in str(exc.detail)


def test_create_site_and_catalog_type(networks_client, seeded_db):
    made = networks_client.post(
        "/racks/sites", data={"name": "Sydney George St", "notes": "new fitout"}, follow_redirects=False
    )
    assert made.status_code == 303
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Sydney George St"))
    assert site is not None

    category = seeded_db.scalar(select(RackItemCategory).where(RackItemCategory.name == "Server"))
    added = networks_client.post(
        "/racks/catalog/types",
        data={
            "category_id": str(category.id),
            "name": "HPE DL380 Gen11",
            "default_ru_height": "2",
            "default_face": "front",
            "default_mount": "ru",
            "default_network_ports": "4",
            "default_power_ports": "2",
            "back_to": "/racks",
        },
        follow_redirects=False,
    )
    assert added.status_code == 303
    item_type = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "HPE DL380 Gen11"))
    assert item_type is not None
    assert item_type.default_ru_height == 2
    assert item_type.default_network_ports == 4


def test_placing_a_vertical_pdu_type_lands_on_a_rail(seeded_db):
    """Regression: the place form forced mount=ru, so a vertical PDU type was
    placed as RU-mounted gear and silently consumed an RU slot."""
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L21")).id)
    pdu_type = seeded_db.scalar(
        select(RackItemType).where(RackItemType.default_mount == "side_pdu")
    )
    assert pdu_type is not None
    placed = rd.place_item(
        seeded_db,
        rack,
        item_type_id=pdu_type.id,
        name="Extra rail PDU",
        ru_start=40,
        ru_height=None,
        face="back",
        mount="",
        side="right",
    )
    seeded_db.flush()
    assert placed.mount == "side_pdu"
    assert placed.ru_height == 0, "a rail PDU must not occupy an RU slot"
    assert placed.side == "right"
    elev = rd.elevation_rows(rd.get_rack(seeded_db, rack.id), "back")
    assert any(i.id == placed.id for i in elev["side_right"])
    assert all(r["item"] is None or r["item"].id != placed.id for r in elev["rows"])


def test_side_pdu_with_blank_side_still_renders(seeded_db):
    """A blank side used to match neither rail, so the item vanished."""
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L21")).id)
    pdu = next(i for i in rack.items if i.mount == "side_pdu")
    pdu.side = ""
    seeded_db.flush()
    elev = rd.elevation_rows(rd.get_rack(seeded_db, rack.id), pdu.face)
    shown = [i.id for i in elev["side_left"]] + [i.id for i in elev["side_right"]]
    assert pdu.id in shown


def test_placing_an_ru_type_is_unaffected(seeded_db):
    """The mount fallback must not turn ordinary gear into a rail PDU."""
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L21")).id)
    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    placed = rd.place_item(
        seeded_db, rack, item_type_id=blank.id, name="Normal blank", ru_start=41, ru_height=1, face="back", mount=""
    )
    seeded_db.flush()
    assert placed.mount == "ru"
    assert placed.ru_height == 1
    assert placed.side == ""


def test_side_pdu_can_be_renamed_and_moved(seeded_db):
    """Vertical PDUs were display-only; they must be editable like other gear."""
    rack = seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26"))
    pdu = next(i for i in rd.get_rack(seeded_db, rack.id).items if i.mount == "side_pdu")
    original_side = pdu.side
    rd.move_item(seeded_db, pdu, ru_start=pdu.ru_start, side="right" if original_side != "right" else "left")
    seeded_db.flush()
    assert pdu.side != original_side
    assert pdu.ru_height == 0, "a side PDU must never take an RU slot"


def test_side_pdu_ignores_ru_collisions(seeded_db):
    """A vertical PDU shares no RU slot, so it must not collide with RU gear."""
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26")).id)
    pdu = next(i for i in rack.items if i.mount == "side_pdu")
    occupied = next(i for i in rack.items if i.mount == "ru")
    rd.move_item(seeded_db, pdu, ru_start=occupied.ru_start, side=pdu.side)
    seeded_db.flush()
    assert pdu.ru_start == occupied.ru_start


def test_move_keeps_item_face_when_not_specified(seeded_db):
    """Regression: the move form used to post the view face and flip the item."""
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26")).id)
    back_item = next((i for i in rack.items if i.mount == "ru" and i.face == "back"), None)
    if back_item is None:
        blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
        back_item = rd.place_item(
            seeded_db, rack, item_type_id=blank.id, name="Back blank", ru_start=44, ru_height=1, face="back"
        )
        seeded_db.flush()
    rd.move_item(seeded_db, back_item, ru_start=back_item.ru_start, face=None)
    assert back_item.face == "back"


def test_move_can_change_face(seeded_db):
    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26")).id)
    item = next(i for i in rack.items if i.mount == "ru" and i.face == "front")
    rd.move_item(seeded_db, item, ru_start=item.ru_start, face="back")
    assert item.face == "back"


def test_move_rejects_bad_face(seeded_db):
    from fastapi import HTTPException

    rack = rd.get_rack(seeded_db, seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26")).id)
    item = next(i for i in rack.items if i.mount == "ru")
    try:
        rd.move_item(seeded_db, item, ru_start=item.ru_start, face="sideways")
        raise AssertionError("expected rejection")
    except HTTPException as exc:
        assert "front, back or both" in str(exc.detail)


def test_cs_can_edit_layout_but_not_create_racks(cs_client, seeded_db):
    """Seeded CS gets view + edit_layout, not rack_manage_racks."""
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    denied = cs_client.post(
        f"/racks/sites/{site.id}/racks",
        data={"name": "CS made this", "ru_height": "10"},
        follow_redirects=False,
    )
    assert denied.status_code == 403
    assert seeded_db.scalar(select(Rack).where(Rack.name == "CS made this")) is None

    rack = seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26"))
    page = cs_client.get(f"/racks/{rack.id}")
    assert page.status_code == 200
    assert "Place item" in page.text
    assert "Rack settings" not in page.text


def test_shift_rack_reorders_left_to_right(seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    order = lambda: [  # noqa: E731
        r.name
        for r in seeded_db.scalars(
            select(Rack).where(Rack.site_id == site.id).order_by(Rack.sort_order, Rack.name)
        ).all()
    ]
    before = order()
    second = seeded_db.scalar(select(Rack).where(Rack.site_id == site.id, Rack.name == before[1]))
    rd.shift_rack(seeded_db, second, direction=-1)
    seeded_db.flush()
    after = order()
    assert after[0] == before[1]
    assert after[1] == before[0]
    assert sorted(after) == sorted(before), "shifting must not add or drop racks"


def test_shift_rack_at_edge_is_a_noop(seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    racks = list(
        seeded_db.scalars(
            select(Rack).where(Rack.site_id == site.id).order_by(Rack.sort_order, Rack.name)
        ).all()
    )
    first = racks[0]
    rd.shift_rack(seeded_db, first, direction=-1)
    seeded_db.flush()
    again = list(
        seeded_db.scalars(
            select(Rack).where(Rack.site_id == site.id).order_by(Rack.sort_order, Rack.name)
        ).all()
    )
    assert again[0].id == first.id


def test_rename_site_and_refuse_delete_while_racks_exist(seeded_db):
    from fastapi import HTTPException

    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    rd.update_site(seeded_db, site, name="Brisbane Albert Street")
    assert site.name == "Brisbane Albert Street"
    try:
        rd.delete_site(seeded_db, site)
        raise AssertionError("expected refusal")
    except HTTPException as exc:
        assert "still has" in str(exc.detail)


def test_delete_empty_site(seeded_db):
    site = rd.create_site(seeded_db, name="Empty site")
    seeded_db.flush()
    rd.delete_site(seeded_db, site)
    seeded_db.flush()
    assert seeded_db.scalar(select(RackSite).where(RackSite.name == "Empty site")) is None


def test_cs_cannot_reorder_or_rename_site(cs_client, seeded_db):
    site = seeded_db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))
    rack = seeded_db.scalar(select(Rack).where(Rack.name == "FDR L26"))
    assert cs_client.post(f"/racks/{rack.id}/shift", data={"direction": "left"}, follow_redirects=False).status_code == 403
    assert cs_client.post(f"/racks/sites/{site.id}/update", data={"name": "Nope"}, follow_redirects=False).status_code == 403


def test_catalog_page_lists_types_with_usage(networks_client, seeded_db):
    page = networks_client.get("/racks/catalog")
    assert page.status_code == 200
    assert "Rack catalog" in page.text
    assert "Blanking - Spare" in page.text


def test_cannot_delete_placed_item_type(seeded_db):
    from fastapi import HTTPException

    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    assert rd.placed_count(seeded_db, blank) > 0
    try:
        rd.delete_item_type(seeded_db, blank)
        raise AssertionError("expected refusal")
    except HTTPException as exc:
        assert "is placed on" in str(exc.detail)
    assert seeded_db.scalar(select(RackItemType).where(RackItemType.id == blank.id)) is not None


def test_can_delete_unused_item_type(seeded_db):
    category = seeded_db.scalar(select(RackItemCategory).where(RackItemCategory.name == "Server"))
    made = rd.create_item_type(seeded_db, category_id=category.id, name="Temp unused type")
    seeded_db.flush()
    assert rd.placed_count(seeded_db, made) == 0
    rd.delete_item_type(seeded_db, made)
    seeded_db.flush()
    assert seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Temp unused type")) is None


def test_rename_item_type_rejects_duplicate(seeded_db):
    from fastapi import HTTPException

    category = seeded_db.scalar(select(RackItemCategory).where(RackItemCategory.name == "Server"))
    first = rd.create_item_type(seeded_db, category_id=category.id, name="Type A")
    second = rd.create_item_type(seeded_db, category_id=category.id, name="Type B")
    seeded_db.flush()
    rd.update_item_type(seeded_db, first, name="Type A renamed")
    assert first.name == "Type A renamed"
    try:
        rd.update_item_type(seeded_db, second, name="Type A renamed")
        raise AssertionError("expected duplicate refusal")
    except HTTPException as exc:
        assert "already has" in str(exc.detail)


def test_cannot_delete_category_with_types(seeded_db):
    from fastapi import HTTPException

    category = seeded_db.scalar(select(RackItemCategory).where(RackItemCategory.name == "Switch"))
    try:
        rd.delete_category(seeded_db, category)
        raise AssertionError("expected refusal")
    except HTTPException as exc:
        assert "still holds" in str(exc.detail)


def test_cs_cannot_manage_catalog(cs_client, seeded_db):
    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    denied = cs_client.post(f"/racks/catalog/types/{blank.id}/delete", follow_redirects=False)
    assert denied.status_code == 403


def test_networks_rack_permissions_page(networks_client, seeded_db):
    r = networks_client.get("/admin/rack-permissions")
    assert r.status_code == 200
    assert "Rack Design permissions" in r.text
    assert RACK_CAP_EDIT_LAYOUT in r.text or "Edit physical layout" in r.text
