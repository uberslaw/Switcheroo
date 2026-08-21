from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import FieldOutlet, Patch, PatchPanel, PatchPanelPort, Port, Switch
from app.services.office import office_slug


def is_third_aux(switch: Switch) -> bool:
    """BNE-L27-AUX-03 — empty of field outlets, cable routing above and below."""
    name = (switch.name or "").upper()
    if name.endswith("AUX-03") or "AUX-03" in name:
        return True
    return (switch.stack_role or "") == "aux" and int(switch.member_number or 0) == 3


def is_available_switch_port(port: Port, patched_port_ids: set[int]) -> bool:
    if port.id in patched_port_ids:
        return False
    if (port.admin_status or "").lower() == "down":
        return False
    if bool(port.faulty):
        return False
    if (port.purpose or "") == "uplink":
        return False
    return True


def patched_switch_port_ids(db: Session) -> set[int]:
    return set(db.scalars(select(Patch.port_id)).all())


def available_ports(db: Session, switch: Switch) -> list[Port]:
    return available_ports_on_switches(db, [switch])


def available_ports_on_switches(db: Session, switches: list[Switch]) -> list[Port]:
    patched = patched_switch_port_ids(db)
    ids = [s.id for s in switches]
    if not ids:
        return []
    ports = list(
        db.scalars(select(Port).where(Port.switch_id.in_(ids)).order_by(Port.switch_id, Port.if_index)).all()
    )
    return [p for p in ports if is_available_switch_port(p, patched)]


def patch_for_port(db: Session, port_id: int) -> Patch | None:
    return db.scalar(
        select(Patch)
        .options(
            selectinload(Patch.field_outlet),
            selectinload(Patch.panel_port),
            selectinload(Patch.port).selectinload(Port.switch),
        )
        .where(Patch.port_id == port_id)
    )


def patching_stacks(office) -> list:
    stacks = list(office.floor_stacks)
    if office.mcr is not None:
        aux = next((st for st in office.mcr.stacks if st.role == "aux"), None)
        if aux is not None:
            stacks.append(aux)
    return stacks


def stack_key(stack) -> str:
    return office_slug(stack.name)


def find_patching_stack(office, key: str | None):
    stacks = patching_stacks(office)
    if not stacks:
        return None
    wanted = office_slug(key or "")
    if wanted:
        for stack in stacks:
            if stack_key(stack) == wanted:
                return stack
    return stacks[0]


def stack_for_outlet(office, outlet: FieldOutlet | None, panels: list[PatchPanel]):
    if outlet is None:
        return find_patching_stack(office, None)
    if outlet.patch is not None and outlet.patch.port is not None:
        name = outlet.patch.port.switch.stack_name
        found = find_patching_stack(office, name)
        if found is not None and stack_key(found) == office_slug(name):
            return found
    for panel in panels:
        if panel.switch is None:
            continue
        for jack in panel.ports:
            if jack.field_outlet_id == outlet.id:
                return find_patching_stack(office, panel.switch.stack_name)
    if (outlet.floor or "").upper() == "AUX":
        return find_patching_stack(office, "Level 27 Aux Stack")
    floor_map = {
        "L27": "Level 27 Floor Stack",
        "L26": "Level 26 Floor Stack",
        "L21": "Level 21 Floor Stack",
    }
    return find_patching_stack(office, floor_map.get((outlet.floor or "").upper()))


def find_outlet(db: Session, code: str) -> FieldOutlet | None:
    needle = (code or "").strip().upper()
    if not needle:
        return None
    return db.scalar(select(FieldOutlet).where(FieldOutlet.code == needle))


def search_outlets(db: Session, location: str, q: str, limit: int = 12) -> list[FieldOutlet]:
    needle = (q or "").strip()
    stmt = select(FieldOutlet)
    if location == "Brisbane":
        stmt = stmt.where(FieldOutlet.floor.in_(("L21", "L26", "L27", "AUX")))
    if needle:
        like = f"%{needle}%"
        stmt = stmt.where(or_(FieldOutlet.code.ilike(like), FieldOutlet.label.ilike(like)))
    stmt = stmt.order_by(FieldOutlet.code).limit(limit)
    return list(db.scalars(stmt).all())


def outlet_with_patch(db: Session, outlet_id: int) -> FieldOutlet | None:
    return db.scalar(
        select(FieldOutlet)
        .options(
            selectinload(FieldOutlet.patch).selectinload(Patch.port).selectinload(Port.switch),
            selectinload(FieldOutlet.patch).selectinload(Patch.panel_port),
        )
        .where(FieldOutlet.id == outlet_id)
    )


def panels_for_location(db: Session, location: str) -> list[PatchPanel]:
    return list(
        db.scalars(
            select(PatchPanel)
            .options(
                selectinload(PatchPanel.ports).selectinload(PatchPanelPort.patch),
                selectinload(PatchPanel.ports).selectinload(PatchPanelPort.field_outlet),
                selectinload(PatchPanel.switch),
            )
            .where(PatchPanel.location == location)
            .order_by(PatchPanel.name)
        ).all()
    )


def jack_outlets(db: Session, panels: list[PatchPanel]) -> dict[int, FieldOutlet]:
    """Map panel-port id → field outlet (jack label / FO code)."""
    mapped: dict[int, FieldOutlet] = {}
    needed: list[int] = []
    for panel in panels:
        for jack in panel.ports:
            if jack.field_outlet is not None:
                mapped[jack.id] = jack.field_outlet
            elif jack.field_outlet_id:
                needed.append(jack.field_outlet_id)
    if needed:
        outlets = {
            o.id: o for o in db.scalars(select(FieldOutlet).where(FieldOutlet.id.in_(needed))).all()
        }
        for panel in panels:
            for jack in panel.ports:
                if jack.id not in mapped and jack.field_outlet_id in outlets:
                    mapped[jack.id] = outlets[jack.field_outlet_id]
    return mapped


def patching_bays(office, panels: list[PatchPanel]) -> list[dict]:
    """Floor stacks plus aux: FO sandwich, or cable managers on aux #3."""
    by_switch: dict[int, dict[str, PatchPanel]] = {}
    for panel in panels:
        if not panel.switch_id:
            continue
        by_switch.setdefault(panel.switch_id, {})[panel.placement or ""] = panel
    bays: list[dict] = []
    for stack in office.floor_stacks:
        bays.append(
            {
                "stack": stack,
                "kind": "floor",
                "blurb": "panel / switch / panel, 20 cm to the adjacent RU",
                "rows": [_bay_row(switch, by_switch, managers=False) for switch in stack.members],
            }
        )
    aux = None
    if office.mcr is not None:
        aux = next((st for st in office.mcr.stacks if st.role == "aux"), None)
    if aux is not None:
        rows = []
        for switch in aux.members:
            # Physical top is #3 (cable routing only); #1 and #2 have FO top and bottom.
            managers = is_third_aux(switch)
            rows.append(_bay_row(switch, by_switch, managers=managers))
        bays.append(
            {
                "stack": aux,
                "kind": "aux",
                "blurb": "#3 on top has cable routing above and below; #1 and #2 have FO in the next RU",
                "rows": rows,
            }
        )
    return bays


def _bay_row(switch: Switch, by_switch: dict[int, dict[str, PatchPanel]], *, managers: bool) -> dict:
    placed = by_switch.get(switch.id, {})
    return {
        "switch": switch,
        "above": None if managers else placed.get("above"),
        "below": None if managers else placed.get("below"),
        "manager_above": managers,
        "manager_below": managers,
        "port_hint": (
            "cable routing above and below · 3rd aux · empty (no FO)"
            if managers
            else "Gi1/0/1–24 up · Gi1/0/25–48 down"
        ),
    }


def switches_for_location(db: Session, switches: list[Switch], location: str) -> list[Switch]:
    return [s for s in switches if (s.location or "") == location]
