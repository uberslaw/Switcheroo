from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ROLE_NETWORKS,
    RACK_CAP_EDIT_LAYOUT,
    RACK_CAP_MANAGE_CATALOG,
    RACK_CAP_MANAGE_PERMISSIONS,
    RACK_CAP_MANAGE_RACKS,
    RACK_CAP_VIEW,
    RACK_CAPABILITIES,
    RACK_FACE_BACK,
    RACK_FACE_BOTH,
    RACK_FACE_FRONT,
    RACK_MOUNT_RU,
    RACK_MOUNT_SIDE_PDU,
    Rack,
    RackItem,
    RackItemCategory,
    RackItemType,
    RackSite,
    User,
    UserRackPermission,
)

CAP_LABELS = {
    RACK_CAP_VIEW: "View rack design",
    RACK_CAP_EDIT_LAYOUT: "Edit physical layout (place / move / rename items)",
    RACK_CAP_MANAGE_CATALOG: "Manage catalog (categories & item types)",
    RACK_CAP_MANAGE_RACKS: "Manage racks (create / rename / RU height)",
    RACK_CAP_MANAGE_PERMISSIONS: "Manage rack permissions",
}


def user_capabilities(db: Session, user: User) -> set[str]:
    if user.role == ROLE_NETWORKS:
        return set(RACK_CAPABILITIES)
    rows = db.scalars(select(UserRackPermission).where(UserRackPermission.user_id == user.id)).all()
    return {r.capability for r in rows}


def has_cap(db: Session, user: User, capability: str) -> bool:
    return capability in user_capabilities(db, user)


def require_cap(db: Session, user: User, capability: str) -> None:
    if not has_cap(db, user, capability):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing rack permission: {CAP_LABELS.get(capability, capability)}",
        )


def ensure_default_rack_permissions(db: Session) -> int:
    """Grant CS users view + edit_layout if they have no rack perms yet."""
    from app.models import ROLE_CS

    created = 0
    cs_users = list(db.scalars(select(User).where(User.role == ROLE_CS)).all())
    for user in cs_users:
        existing = {
            p.capability
            for p in db.scalars(select(UserRackPermission).where(UserRackPermission.user_id == user.id)).all()
        }
        for cap in (RACK_CAP_VIEW, RACK_CAP_EDIT_LAYOUT):
            if cap not in existing:
                db.add(UserRackPermission(user_id=user.id, capability=cap))
                created += 1
    db.flush()
    return created


def set_user_capability(db: Session, user_id: int, capability: str, granted: bool) -> None:
    if capability not in RACK_CAPABILITIES:
        raise HTTPException(status_code=400, detail="Unknown capability")
    row = db.scalar(
        select(UserRackPermission).where(
            UserRackPermission.user_id == user_id,
            UserRackPermission.capability == capability,
        )
    )
    if granted and row is None:
        db.add(UserRackPermission(user_id=user_id, capability=capability))
    elif not granted and row is not None:
        db.delete(row)


def list_sites(db: Session) -> list[RackSite]:
    return list(
        db.scalars(
            select(RackSite).options(selectinload(RackSite.racks)).order_by(RackSite.sort_order, RackSite.name)
        ).all()
    )


def get_site(db: Session, site_id: int) -> RackSite:
    site = db.scalar(
        select(RackSite)
        .where(RackSite.id == site_id)
        .options(
            selectinload(RackSite.racks)
            .selectinload(Rack.items)
            .selectinload(RackItem.item_type)
            .selectinload(RackItemType.category)
        )
        .execution_options(populate_existing=True)
    )
    if site is None:
        raise HTTPException(status_code=404, detail="Site not found")
    return site


def get_rack(db: Session, rack_id: int) -> Rack:
    rack = db.scalar(
        select(Rack)
        .where(Rack.id == rack_id)
        .options(
            selectinload(Rack.site),
            selectinload(Rack.items).selectinload(RackItem.item_type).selectinload(RackItemType.category),
        )
        .execution_options(populate_existing=True)
    )
    if rack is None:
        raise HTTPException(status_code=404, detail="Rack not found")
    return rack


def catalog_tree(db: Session) -> list[RackItemCategory]:
    return list(
        db.scalars(
            select(RackItemCategory)
            .options(selectinload(RackItemCategory.types))
            .order_by(RackItemCategory.sort_order, RackItemCategory.name)
        ).all()
    )


def _faces_overlap(a: str, b: str) -> bool:
    if a == RACK_FACE_BOTH or b == RACK_FACE_BOTH:
        return True
    return a == b


def item_occupies_ru(item: RackItem, ru: int, face: str) -> bool:
    if item.mount != RACK_MOUNT_RU:
        return False
    if not _faces_overlap(item.face, face):
        return False
    bottom = item.ru_end
    top = item.ru_start
    return bottom <= ru <= top


def find_collision(db: Session, rack: Rack, *, face: str, ru_start: int, ru_height: int, ignore_id: int | None = None) -> RackItem | None:
    if ru_height < 1:
        return None
    bottom = ru_start - ru_height + 1
    if bottom < 1 or ru_start > rack.ru_height:
        raise HTTPException(status_code=400, detail="Item does not fit in rack RU range")
    items = list(
        db.scalars(select(RackItem).where(RackItem.rack_id == rack.id)).all()
    )
    for item in items:
        if ignore_id is not None and item.id == ignore_id:
            continue
        if item.mount != RACK_MOUNT_RU:
            continue
        if not _faces_overlap(item.face, face):
            continue
        other_bottom = item.ru_end
        other_top = item.ru_start
        if not (ru_start < other_bottom or bottom > other_top):
            return item
    return None


def place_item(
    db: Session,
    rack: Rack,
    *,
    item_type_id: int,
    name: str,
    ru_start: int,
    ru_height: int | None,
    face: str,
    mount: str = RACK_MOUNT_RU,
    side: str = "",
    management_ip: str = "",
    network_ports: int | None = None,
    power_ports: int | None = None,
    notes: str = "",
) -> RackItem:
    item_type = db.get(RackItemType, item_type_id)
    if item_type is None:
        raise HTTPException(status_code=400, detail="Unknown item type")
    height = ru_height if ru_height is not None else (item_type.default_ru_height or 1)
    face = face or item_type.default_face or RACK_FACE_FRONT
    mount = mount or item_type.default_mount or RACK_MOUNT_RU
    if mount == RACK_MOUNT_RU:
        hit = find_collision(db, rack, face=face, ru_start=ru_start, ru_height=max(1, height))
        if hit is not None:
            raise HTTPException(status_code=400, detail=f"Collides with {hit.name or hit.item_type.name}")
    item = RackItem(
        rack_id=rack.id,
        item_type_id=item_type.id,
        name=(name or item_type.name).strip(),
        ru_start=ru_start,
        ru_height=0 if mount == RACK_MOUNT_SIDE_PDU else max(1, height),
        face=face,
        mount=mount,
        side=side,
        management_ip=management_ip.strip(),
        network_ports=item_type.default_network_ports if network_ports is None else network_ports,
        power_ports=item_type.default_power_ports if power_ports is None else power_ports,
        notes=notes.strip(),
    )
    db.add(item)
    db.flush()
    return item


def move_item(db: Session, item: RackItem, *, ru_start: int, face: str | None = None, ru_height: int | None = None) -> RackItem:
    rack = get_rack(db, item.rack_id)
    new_face = face or item.face
    new_height = item.ru_height if ru_height is None else ru_height
    if item.mount == RACK_MOUNT_RU:
        hit = find_collision(
            db,
            rack,
            face=new_face,
            ru_start=ru_start,
            ru_height=max(1, new_height),
            ignore_id=item.id,
        )
        if hit is not None:
            raise HTTPException(status_code=400, detail=f"Collides with {hit.name or hit.item_type.name}")
    item.ru_start = ru_start
    item.face = new_face
    if ru_height is not None:
        item.ru_height = max(1, ru_height) if item.mount == RACK_MOUNT_RU else 0
    db.flush()
    return item


def elevation_rows(rack: Rack, face: str) -> dict:
    """Build top→bottom RU rows for the elevation UI (doc numbering: high RU at top)."""
    rows = []
    for ru in range(rack.ru_height, 0, -1):
        occupants = [
            item
            for item in rack.items
            if item.mount == RACK_MOUNT_RU and item_occupies_ru(item, ru, face)
        ]
        # Only render the item on its top RU cell (rowspan via ru_height)
        primary = None
        for item in occupants:
            if item.ru_start == ru:
                primary = item
                break
        cont = False
        if primary is None:
            cont = any(item.ru_start != ru for item in occupants)
        rows.append({"ru": ru, "item": primary, "continuation": cont})
    side_left = [i for i in rack.items if i.mount == RACK_MOUNT_SIDE_PDU and i.side == "left"]
    side_right = [i for i in rack.items if i.mount == RACK_MOUNT_SIDE_PDU and i.side == "right"]
    return {"rows": rows, "side_left": side_left, "side_right": side_right}
