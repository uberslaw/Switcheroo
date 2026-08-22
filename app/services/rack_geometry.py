"""Room geometry and cable length estimation for Rack Design.

Every length here is millimetres, stored and returned as an integer. There is
no metre conversion anywhere, so nothing needs unit-juggling.

The estimate is deliberately "shortest cable that will actually reach" rather
than a precise figure: a cable is not made to measure, so the number carries a
contingency and a per-bend allowance, and every result shows its segments so it
can be sanity-checked against a tape measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    RACK_ENTRY_BOTH,
    RACK_ENTRY_BOTTOM,
    RACK_ENTRY_TOP,
    RU_MM,
    Rack,
    RackItem,
    RackRoom,
    RackSite,
)

# Tuning defaults. Meant to be adjusted once someone measures a real run.
DEFAULT_CONTINGENCY_PCT = 10.0
# A right-angle turn eats length through its bend radius rather than turning
# on a point. Applied per bend, to copper and fibre alike.
DEFAULT_BEND_ALLOWANCE_MM = 150
# Left coiled at each termination.
DEFAULT_SERVICE_LOOP_MM = 300

CABLE_ENTRIES = (RACK_ENTRY_TOP, RACK_ENTRY_BOTTOM, RACK_ENTRY_BOTH)


@dataclass
class Segment:
    label: str
    length_mm: int


@dataclass
class LengthEstimate:
    """A cable estimate with its working shown."""

    segments: list[Segment] = field(default_factory=list)
    contingency_pct: float = DEFAULT_CONTINGENCY_PCT
    bends: int = 0
    bend_allowance_mm: int = DEFAULT_BEND_ALLOWANCE_MM
    service_loop_mm: int = DEFAULT_SERVICE_LOOP_MM
    ends: int = 2

    @property
    def route_mm(self) -> int:
        return sum(s.length_mm for s in self.segments)

    @property
    def bends_mm(self) -> int:
        return self.bends * self.bend_allowance_mm

    @property
    def loops_mm(self) -> int:
        return self.ends * self.service_loop_mm

    @property
    def subtotal_mm(self) -> int:
        return self.route_mm + self.bends_mm + self.loops_mm

    @property
    def contingency_mm(self) -> int:
        return int(round(self.subtotal_mm * self.contingency_pct / 100.0))

    @property
    def total_mm(self) -> int:
        return self.subtotal_mm + self.contingency_mm

    def as_rows(self) -> list[tuple[str, int]]:
        """Flat breakdown for display, in the order it is built up."""
        rows = [(s.label, s.length_mm) for s in self.segments]
        if self.bends:
            rows.append((f"{self.bends} bend{'s' if self.bends != 1 else ''}", self.bends_mm))
        if self.loops_mm:
            rows.append((f"service loop × {self.ends}", self.loops_mm))
        rows.append((f"contingency {self.contingency_pct:g}%", self.contingency_mm))
        return rows


def item_height_mm(rack: Rack, item: RackItem) -> int:
    """Height of an item's midpoint above the rack floor.

    RU numbering is document-style with RU1 at the bottom, so RU n sits
    (n - 1) units up. ru_start is the item's top RU and ru_end its bottom.
    """
    bottom_ru = item.ru_end if item.ru_height else item.ru_start
    bottom_mm = rack.plinth_mm + (bottom_ru - 1) * RU_MM
    span = max(1, item.ru_height) * RU_MM
    return int(round(bottom_mm + span / 2))


def entry_height_mm(rack: Rack, *, prefer: str | None = None) -> int:
    """Height at which cable leaves the shell."""
    entry = prefer or rack.cable_entry or RACK_ENTRY_TOP
    if entry == RACK_ENTRY_BOTTOM:
        return 0
    if entry == RACK_ENTRY_BOTH:
        return rack.external_height_mm
    return rack.external_height_mm


def rack_centre_mm(rack: Rack) -> tuple[int, int]:
    """Plan centre of the rack footprint, accounting for rotation."""
    if rack.rotation_deg % 180 == 90:
        width, depth = rack.depth_mm, rack.width_mm
    else:
        width, depth = rack.width_mm, rack.depth_mm
    return rack.pos_x_mm + width // 2, rack.pos_y_mm + depth // 2


def _tray_height(room: RackRoom | None) -> int:
    if room is None:
        return 0
    if room.tray_height_mm:
        return room.tray_height_mm
    # Fall back to just under the ceiling rather than guessing zero.
    return max(0, room.ceiling_height_mm - 300)


def estimate_rack_to_rack(
    db: Session,
    from_item: RackItem,
    to_item: RackItem,
    *,
    contingency_pct: float | None = None,
    bend_allowance_mm: int | None = None,
    service_loop_mm: int | None = None,
) -> LengthEstimate:
    """Rise to tray, rectilinear across, drop at the far end.

    Same-rack runs skip the tray entirely and stay inside the shell.
    """
    from_rack = db.get(Rack, from_item.rack_id)
    to_rack = db.get(Rack, to_item.rack_id)
    if from_rack is None or to_rack is None:
        raise HTTPException(status_code=404, detail="Rack not found")

    estimate = LengthEstimate(
        contingency_pct=DEFAULT_CONTINGENCY_PCT if contingency_pct is None else contingency_pct,
        bend_allowance_mm=DEFAULT_BEND_ALLOWANCE_MM if bend_allowance_mm is None else bend_allowance_mm,
        service_loop_mm=DEFAULT_SERVICE_LOOP_MM if service_loop_mm is None else service_loop_mm,
    )
    from_h = item_height_mm(from_rack, from_item)
    to_h = item_height_mm(to_rack, to_item)

    if from_rack.id == to_rack.id:
        # Inside one shell: straight up or down the rack, no tray leg.
        estimate.segments.append(Segment("vertical inside rack", abs(from_h - to_h)))
        # Across the rack width to reach the other side's rail.
        estimate.segments.append(Segment("across rack", from_rack.width_mm // 2))
        estimate.bends = 2
        return estimate

    room = from_rack.rack_room or to_rack.rack_room
    tray = _tray_height(room)
    from_entry = entry_height_mm(from_rack)
    to_entry = entry_height_mm(to_rack)

    estimate.segments.append(Segment("rise to cable entry", abs(from_entry - from_h)))
    estimate.segments.append(Segment("entry up to tray", max(0, tray - from_entry)))

    fx, fy = rack_centre_mm(from_rack)
    tx, ty = rack_centre_mm(to_rack)
    estimate.segments.append(Segment("along tray (X)", abs(tx - fx)))
    estimate.segments.append(Segment("along tray (Y)", abs(ty - fy)))

    estimate.segments.append(Segment("tray down to entry", max(0, tray - to_entry)))
    estimate.segments.append(Segment("entry down to item", abs(to_entry - to_h)))

    # Up, along X, along Y, down, then in at each end.
    estimate.bends = 6
    return estimate


def estimate_item_to_point(
    db: Session,
    item: RackItem,
    *,
    x_mm: int,
    y_mm: int,
    z_mm: int,
    contingency_pct: float | None = None,
    bend_allowance_mm: int | None = None,
    service_loop_mm: int | None = None,
) -> LengthEstimate:
    """Item to a fixed point such as a ceiling power source or an outlet."""
    rack = db.get(Rack, item.rack_id)
    if rack is None:
        raise HTTPException(status_code=404, detail="Rack not found")
    estimate = LengthEstimate(
        contingency_pct=DEFAULT_CONTINGENCY_PCT if contingency_pct is None else contingency_pct,
        bend_allowance_mm=DEFAULT_BEND_ALLOWANCE_MM if bend_allowance_mm is None else bend_allowance_mm,
        service_loop_mm=DEFAULT_SERVICE_LOOP_MM if service_loop_mm is None else service_loop_mm,
    )
    item_h = item_height_mm(rack, item)
    entry = entry_height_mm(rack)
    rx, ry = rack_centre_mm(rack)

    estimate.segments.append(Segment("rise to cable entry", abs(entry - item_h)))
    estimate.segments.append(Segment("entry to source height", abs(z_mm - entry)))
    estimate.segments.append(Segment("horizontal (X)", abs(x_mm - rx)))
    estimate.segments.append(Segment("horizontal (Y)", abs(y_mm - ry)))
    estimate.bends = 4
    return estimate


# --- Room CRUD -------------------------------------------------------------


def list_rooms(db: Session, site_id: int) -> list[RackRoom]:
    return list(
        db.scalars(
            select(RackRoom).where(RackRoom.site_id == site_id).order_by(RackRoom.floor, RackRoom.name)
        ).all()
    )


def get_room(db: Session, room_id: int) -> RackRoom:
    room = db.get(RackRoom, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return room


def _validate_room(*, ceiling_height_mm: int, tray_height_mm: int) -> None:
    if ceiling_height_mm and not 1000 <= ceiling_height_mm <= 20000:
        raise HTTPException(status_code=400, detail="Ceiling height must be between 1000 and 20000 mm")
    if tray_height_mm and ceiling_height_mm and tray_height_mm > ceiling_height_mm:
        raise HTTPException(status_code=400, detail="Tray cannot sit above the ceiling")


def create_room(
    db: Session,
    *,
    site_id: int,
    name: str,
    floor: str = "",
    width_mm: int = 0,
    length_mm: int = 0,
    ceiling_height_mm: int = 2700,
    tray_height_mm: int = 2400,
    front_clearance_mm: int = 1000,
    notes: str = "",
) -> RackRoom:
    site = db.get(RackSite, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    clean = name.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Room name is required")
    if db.scalar(select(RackRoom).where(RackRoom.site_id == site_id, RackRoom.name == clean)):
        raise HTTPException(status_code=400, detail=f"{site.name} already has a room called {clean}")
    _validate_room(ceiling_height_mm=ceiling_height_mm, tray_height_mm=tray_height_mm)
    room = RackRoom(
        site_id=site_id,
        name=clean,
        floor=floor.strip(),
        width_mm=max(0, width_mm),
        length_mm=max(0, length_mm),
        ceiling_height_mm=ceiling_height_mm,
        tray_height_mm=tray_height_mm,
        front_clearance_mm=max(0, front_clearance_mm),
        notes=notes.strip(),
    )
    db.add(room)
    db.flush()
    return room


def update_room(
    db: Session,
    room: RackRoom,
    *,
    name: str | None = None,
    floor: str | None = None,
    width_mm: int | None = None,
    length_mm: int | None = None,
    ceiling_height_mm: int | None = None,
    tray_height_mm: int | None = None,
    front_clearance_mm: int | None = None,
    notes: str | None = None,
) -> RackRoom:
    if name is not None:
        clean = name.strip()
        if not clean:
            raise HTTPException(status_code=400, detail="Room name is required")
        clash = db.scalar(
            select(RackRoom).where(
                RackRoom.site_id == room.site_id, RackRoom.name == clean, RackRoom.id != room.id
            )
        )
        if clash is not None:
            raise HTTPException(status_code=400, detail=f"Another room here is called {clean}")
        room.name = clean
    if floor is not None:
        room.floor = floor.strip()
    if width_mm is not None:
        room.width_mm = max(0, width_mm)
    if length_mm is not None:
        room.length_mm = max(0, length_mm)
    ceiling = room.ceiling_height_mm if ceiling_height_mm is None else ceiling_height_mm
    tray = room.tray_height_mm if tray_height_mm is None else tray_height_mm
    _validate_room(ceiling_height_mm=ceiling, tray_height_mm=tray)
    room.ceiling_height_mm = ceiling
    room.tray_height_mm = tray
    if front_clearance_mm is not None:
        room.front_clearance_mm = max(0, front_clearance_mm)
    if notes is not None:
        room.notes = notes.strip()
    db.flush()
    return room


def delete_room(db: Session, room: RackRoom) -> None:
    """Racks survive; they simply lose their position."""
    count = db.scalar(select(func.count(Rack.id)).where(Rack.rack_room_id == room.id)) or 0
    for rack in db.scalars(select(Rack).where(Rack.rack_room_id == room.id)).all():
        rack.rack_room_id = None
    db.delete(room)
    db.flush()
    if count:
        # Not an error: the caller reports how many racks were detached.
        return


def set_rack_geometry(
    db: Session,
    rack: Rack,
    *,
    rack_room_id: int | None = None,
    width_mm: int | None = None,
    depth_mm: int | None = None,
    plinth_mm: int | None = None,
    roof_mm: int | None = None,
    pos_x_mm: int | None = None,
    pos_y_mm: int | None = None,
    rotation_deg: int | None = None,
    cable_entry: str | None = None,
) -> Rack:
    if rack_room_id is not None:
        if rack_room_id == 0:
            rack.rack_room_id = None
        else:
            room = get_room(db, rack_room_id)
            if room.site_id != rack.site_id:
                raise HTTPException(status_code=400, detail="That room belongs to another site")
            rack.rack_room_id = room.id
    for value, attr, label in (
        (width_mm, "width_mm", "Width"),
        (depth_mm, "depth_mm", "Depth"),
    ):
        if value is not None:
            if not 100 <= value <= 3000:
                raise HTTPException(status_code=400, detail=f"{label} must be between 100 and 3000 mm")
            setattr(rack, attr, value)
    if plinth_mm is not None:
        if not 0 <= plinth_mm <= 1000:
            raise HTTPException(status_code=400, detail="Plinth must be between 0 and 1000 mm")
        rack.plinth_mm = plinth_mm
    if roof_mm is not None:
        if not 0 <= roof_mm <= 1000:
            raise HTTPException(status_code=400, detail="Roof must be between 0 and 1000 mm")
        rack.roof_mm = roof_mm
    if pos_x_mm is not None:
        rack.pos_x_mm = max(0, pos_x_mm)
    if pos_y_mm is not None:
        rack.pos_y_mm = max(0, pos_y_mm)
    if rotation_deg is not None:
        if rotation_deg not in (0, 90, 180, 270):
            raise HTTPException(status_code=400, detail="Rotation must be 0, 90, 180 or 270 degrees")
        rack.rotation_deg = rotation_deg
    if cable_entry is not None:
        if cable_entry not in CABLE_ENTRIES:
            raise HTTPException(status_code=400, detail="Cable entry must be top, bottom or both")
        rack.cable_entry = cable_entry
    db.flush()
    return rack
