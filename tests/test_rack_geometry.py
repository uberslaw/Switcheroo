from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models import RU_MM, Rack, RackItemType, RackRoom, RackSite
from app.services import rack_design as rd
from app.services import rack_geometry as geo


def _site(db) -> RackSite:
    return db.scalar(select(RackSite).where(RackSite.name == "Brisbane Albert St"))


def _rack(db, name: str = "FDR L26") -> Rack:
    return db.scalar(select(Rack).where(Rack.name == name))


def test_ru_to_height_uses_document_numbering(seeded_db):
    """RU1 is at the bottom, so RU n sits (n - 1) units up from the plinth."""
    rack = _rack(seeded_db)
    rack.plinth_mm = 100
    blank = seeded_db.scalar(select(RackItemType).where(RackItemType.name == "Blanking - Spare"))
    low = rd.place_item(
        seeded_db, rd.get_rack(seeded_db, rack.id), item_type_id=blank.id, name="low",
        ru_start=1, ru_height=1, face="back",
    )
    high = rd.place_item(
        seeded_db, rd.get_rack(seeded_db, rack.id), item_type_id=blank.id, name="high",
        ru_start=41, ru_height=1, face="back",
    )
    seeded_db.flush()
    low_mm = geo.item_height_mm(rack, low)
    high_mm = geo.item_height_mm(rack, high)
    assert low_mm < high_mm, "a higher RU number must be physically higher"
    # RU1 midpoint sits half a unit above the plinth.
    assert low_mm == pytest.approx(100 + RU_MM / 2, abs=1)
    assert high_mm == pytest.approx(100 + 40 * RU_MM + RU_MM / 2, abs=1)


def test_external_height_includes_plinth_and_roof(seeded_db):
    rack = _rack(seeded_db)
    rack.ru_height = 45
    rack.plinth_mm = 100
    rack.roof_mm = 50
    assert rack.ru_span_mm == int(round(45 * RU_MM))
    assert rack.external_height_mm == int(round(100 + 45 * RU_MM + 50))


def test_multi_ru_item_measures_from_its_middle(seeded_db):
    from app.models import RackItem

    tall = seeded_db.scalars(
        select(RackItem).where(RackItem.mount == "ru", RackItem.ru_height >= 3)
    ).first()
    assert tall is not None, "seed should import at least one 3U device (the UPS units)"
    rack = seeded_db.get(Rack, tall.rack_id)
    rack.plinth_mm = 0
    seeded_db.flush()
    height = geo.item_height_mm(rack, tall)
    bottom = (tall.ru_end - 1) * RU_MM
    top = tall.ru_start * RU_MM
    assert bottom < height < top


def test_estimate_shows_its_segments(seeded_db):
    site = _site(seeded_db)
    room = geo.create_room(
        seeded_db, site_id=site.id, name="MCR-geo", floor="L27",
        width_mm=6000, length_mm=8000, ceiling_height_mm=2700, tray_height_mm=2400,
    )
    seeded_db.flush()
    a = geo.set_rack_geometry(
        seeded_db, _rack(seeded_db, "FDR L26"), rack_room_id=room.id, pos_x_mm=0, pos_y_mm=0
    )
    b = geo.set_rack_geometry(
        seeded_db, _rack(seeded_db, "FDR L21"), rack_room_id=room.id, pos_x_mm=3000, pos_y_mm=1000
    )
    seeded_db.flush()
    item_a = next(i for i in rd.get_rack(seeded_db, a.id).items if i.mount == "ru")
    item_b = next(i for i in rd.get_rack(seeded_db, b.id).items if i.mount == "ru")

    est = geo.estimate_rack_to_rack(seeded_db, item_a, item_b)
    labels = [s.label for s in est.segments]
    assert "along tray (X)" in labels
    assert "along tray (Y)" in labels
    # Rectilinear, so the horizontal legs are the full dx and dy, not a diagonal.
    x_leg = next(s.length_mm for s in est.segments if s.label == "along tray (X)")
    y_leg = next(s.length_mm for s in est.segments if s.label == "along tray (Y)")
    assert x_leg == 3000
    assert y_leg == 1000
    assert est.route_mm == sum(s.length_mm for s in est.segments)
    assert est.total_mm > est.route_mm, "bends, loops and contingency must add length"
    rows = est.as_rows()
    assert any("contingency" in label for label, _ in rows)
    assert sum(mm for _, mm in rows) == est.total_mm


def test_contingency_and_bends_are_tunable(seeded_db):
    site = _site(seeded_db)
    room = geo.create_room(seeded_db, site_id=site.id, name="Tune", ceiling_height_mm=2700, tray_height_mm=2400)
    seeded_db.flush()
    a = geo.set_rack_geometry(seeded_db, _rack(seeded_db, "FDR L26"), rack_room_id=room.id)
    b = geo.set_rack_geometry(seeded_db, _rack(seeded_db, "FDR L21"), rack_room_id=room.id, pos_x_mm=2000)
    seeded_db.flush()
    item_a = next(i for i in rd.get_rack(seeded_db, a.id).items if i.mount == "ru")
    item_b = next(i for i in rd.get_rack(seeded_db, b.id).items if i.mount == "ru")

    plain = geo.estimate_rack_to_rack(seeded_db, item_a, item_b, contingency_pct=0, service_loop_mm=0, bend_allowance_mm=0)
    assert plain.total_mm == plain.route_mm
    padded = geo.estimate_rack_to_rack(seeded_db, item_a, item_b, contingency_pct=10)
    assert padded.total_mm > plain.total_mm


def test_same_rack_run_skips_the_tray(seeded_db):
    rack = rd.get_rack(seeded_db, _rack(seeded_db).id)
    items = [i for i in rack.items if i.mount == "ru"]
    est = geo.estimate_rack_to_rack(seeded_db, items[0], items[1])
    labels = [s.label for s in est.segments]
    assert "along tray (X)" not in labels
    assert "vertical inside rack" in labels


def test_estimate_to_a_ceiling_point(seeded_db):
    site = _site(seeded_db)
    room = geo.create_room(seeded_db, site_id=site.id, name="Power", ceiling_height_mm=3000, tray_height_mm=2700)
    seeded_db.flush()
    rack = geo.set_rack_geometry(
        seeded_db, _rack(seeded_db), rack_room_id=room.id, pos_x_mm=1000, pos_y_mm=1000
    )
    seeded_db.flush()
    item = next(i for i in rd.get_rack(seeded_db, rack.id).items if i.mount == "ru")
    est = geo.estimate_item_to_point(seeded_db, item, x_mm=4000, y_mm=1000, z_mm=3000)
    x_leg = next(s.length_mm for s in est.segments if s.label == "horizontal (X)")
    assert x_leg == 4000 - (rack.pos_x_mm + rack.width_mm // 2)
    assert est.total_mm > 0


def test_room_rejects_tray_above_ceiling(seeded_db):
    site = _site(seeded_db)
    with pytest.raises(HTTPException) as err:
        geo.create_room(seeded_db, site_id=site.id, name="Bad", ceiling_height_mm=2400, tray_height_mm=2700)
    assert "above the ceiling" in str(err.value.detail)


def test_room_defaults_match_the_workbook_note(seeded_db):
    """The Albert St MCR sheet asks for 1 m of clearance in front."""
    site = _site(seeded_db)
    room = geo.create_room(seeded_db, site_id=site.id, name="Defaults")
    assert room.front_clearance_mm == 1000


def test_duplicate_room_name_rejected(seeded_db):
    site = _site(seeded_db)
    geo.create_room(seeded_db, site_id=site.id, name="Twice")
    seeded_db.flush()
    with pytest.raises(HTTPException) as err:
        geo.create_room(seeded_db, site_id=site.id, name="Twice")
    assert "already has a room" in str(err.value.detail)


def test_deleting_a_room_detaches_racks_but_keeps_them(seeded_db):
    site = _site(seeded_db)
    room = geo.create_room(seeded_db, site_id=site.id, name="Doomed")
    seeded_db.flush()
    rack = geo.set_rack_geometry(seeded_db, _rack(seeded_db), rack_room_id=room.id)
    seeded_db.flush()
    rack_id = rack.id
    geo.delete_room(seeded_db, room)
    seeded_db.flush()
    survivor = seeded_db.get(Rack, rack_id)
    assert survivor is not None, "deleting a room must not delete its racks"
    assert survivor.rack_room_id is None


def test_geometry_validation(seeded_db):
    rack = _rack(seeded_db)
    for kwargs, expected in (
        ({"width_mm": 50}, "Width must be"),
        ({"depth_mm": 9999}, "Depth must be"),
        ({"rotation_deg": 45}, "Rotation must be"),
        ({"cable_entry": "sideways"}, "Cable entry must be"),
        ({"plinth_mm": 5000}, "Plinth must be"),
    ):
        with pytest.raises(HTTPException) as err:
            geo.set_rack_geometry(seeded_db, rack, **kwargs)
        assert expected in str(err.value.detail)


def test_rotation_swaps_the_footprint(seeded_db):
    rack = _rack(seeded_db)
    geo.set_rack_geometry(seeded_db, rack, width_mm=600, depth_mm=1200, pos_x_mm=0, pos_y_mm=0, rotation_deg=0)
    seeded_db.flush()
    assert geo.rack_centre_mm(rack) == (300, 600)
    geo.set_rack_geometry(seeded_db, rack, rotation_deg=90)
    seeded_db.flush()
    assert geo.rack_centre_mm(rack) == (600, 300)


def test_rack_cannot_join_another_sites_room(seeded_db):
    other = rd.create_site(seeded_db, name="Other site")
    seeded_db.flush()
    foreign = geo.create_room(seeded_db, site_id=other.id, name="Foreign room")
    seeded_db.flush()
    with pytest.raises(HTTPException) as err:
        geo.set_rack_geometry(seeded_db, _rack(seeded_db), rack_room_id=foreign.id)
    assert "another site" in str(err.value.detail)
