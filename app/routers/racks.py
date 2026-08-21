from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.models import (
    RACK_CAP_EDIT_LAYOUT,
    RACK_CAP_MANAGE_CATALOG,
    RACK_CAP_MANAGE_RACKS,
    RACK_CAP_VIEW,
    RACK_FACE_BACK,
    RACK_FACE_FRONT,
    RACK_MOUNT_RU,
    RackItem,
    User,
)
from app.services import rack_design as rd
from app.templating import flash, render

router = APIRouter(prefix="/racks")


def _user(request: Request, db: Session = Depends(get_db)) -> User:
    return require_user(db, request)


@router.get("")
def sites(request: Request, db: Session = Depends(get_db), user: User = Depends(_user)):
    rd.require_cap(db, user, RACK_CAP_VIEW)
    caps = rd.user_capabilities(db, user)
    return render(
        request,
        "racks/sites.html",
        user=user,
        sites=rd.list_sites(db),
        caps=caps,
        can_edit=RACK_CAP_EDIT_LAYOUT in caps,
        can_manage_racks=RACK_CAP_MANAGE_RACKS in caps,
    )


@router.post("/sites")
def create_site(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    name: str = Form(...),
    notes: str = Form(""),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_RACKS)
    try:
        site = rd.create_site(db, name=name, notes=notes)
        db.commit()
        flash(request, f"Created site {site.name}.", "ok")
        return RedirectResponse(f"/racks/sites/{site.id}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse("/racks", status_code=303)


@router.post("/sites/{site_id}/racks")
def create_rack(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    name: str = Form(...),
    floor: str = Form(""),
    room: str = Form(""),
    ru_height: int = Form(45),
    face: str = Form(RACK_FACE_FRONT),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_RACKS)
    try:
        rack = rd.create_rack(
            db,
            site_id=site_id,
            name=name,
            floor=floor,
            room=room,
            ru_height=ru_height,
        )
        db.commit()
        flash(request, f"Created rack {rack.name} ({rack.ru_height}U).", "ok")
        return RedirectResponse(f"/racks/{rack.id}?face={face}", status_code=303)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/sites/{site_id}?face={face}", status_code=303)


@router.post("/sites/{site_id}/reimport")
def reimport_site(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
):
    """Rebuild a site from the seed workbook. Destructive: drops layout edits."""
    rd.require_cap(db, user, RACK_CAP_MANAGE_RACKS)
    site = rd.get_site(db, site_id)
    try:
        from app.services.rack_import import import_brisbane_layout

        stats = import_brisbane_layout(db, force=True)
        db.commit()
        flash(request, f"Re-imported {site.name}: {stats['racks']} racks, {stats['items']} items.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/sites/{site_id}", status_code=303)


@router.get("/sites/{site_id}")
def site_detail(
    site_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    face: str = "front",
):
    rd.require_cap(db, user, RACK_CAP_VIEW)
    site = rd.get_site(db, site_id)
    face = face if face in (RACK_FACE_FRONT, RACK_FACE_BACK) else RACK_FACE_FRONT
    caps = rd.user_capabilities(db, user)
    elevations = []
    for rack in sorted(site.racks, key=lambda r: (r.sort_order, r.name)):
        elev = rd.elevation_rows(rack, face)
        elevations.append({"rack": rack, **elev})
    return render(
        request,
        "racks/site.html",
        user=user,
        site=site,
        face=face,
        elevations=elevations,
        catalog=rd.catalog_tree(db),
        caps=caps,
        can_edit=RACK_CAP_EDIT_LAYOUT in caps,
        can_manage_racks=RACK_CAP_MANAGE_RACKS in caps,
        can_manage_catalog=RACK_CAP_MANAGE_CATALOG in caps,
    )


@router.get("/{rack_id}")
def rack_detail(
    rack_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    face: str = "front",
):
    rd.require_cap(db, user, RACK_CAP_VIEW)
    rack = rd.get_rack(db, rack_id)
    face = face if face in (RACK_FACE_FRONT, RACK_FACE_BACK) else RACK_FACE_FRONT
    caps = rd.user_capabilities(db, user)
    elev = rd.elevation_rows(rack, face)
    return render(
        request,
        "racks/elevation.html",
        user=user,
        site=rack.site,
        rack=rack,
        face=face,
        rows=elev["rows"],
        side_left=elev["side_left"],
        side_right=elev["side_right"],
        catalog=rd.catalog_tree(db),
        caps=caps,
        can_edit=RACK_CAP_EDIT_LAYOUT in caps,
        can_manage_catalog=RACK_CAP_MANAGE_CATALOG in caps,
        can_manage_racks=RACK_CAP_MANAGE_RACKS in caps,
        silhouettes=rd.SILHOUETTES,
    )


@router.post("/catalog/categories")
def create_category(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    name: str = Form(...),
    silhouette: str = Form("generic"),
    back_to: str = Form("/racks"),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_CATALOG)
    try:
        category = rd.create_category(db, name=name, silhouette=silhouette)
        db.commit()
        flash(request, f"Added category {category.name}.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(back_to, status_code=303)


@router.post("/catalog/types")
def create_item_type(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    category_id: int = Form(...),
    name: str = Form(...),
    default_ru_height: int = Form(1),
    default_face: str = Form(RACK_FACE_FRONT),
    default_mount: str = Form(RACK_MOUNT_RU),
    default_network_ports: int = Form(0),
    default_power_ports: int = Form(0),
    back_to: str = Form("/racks"),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_CATALOG)
    try:
        item_type = rd.create_item_type(
            db,
            category_id=category_id,
            name=name,
            default_ru_height=default_ru_height,
            default_face=default_face,
            default_mount=default_mount,
            default_network_ports=default_network_ports,
            default_power_ports=default_power_ports,
        )
        db.commit()
        flash(request, f"Added {item_type.name} to the catalog.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(back_to, status_code=303)


@router.post("/{rack_id}/update")
def update_rack(
    rack_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    name: str = Form(...),
    floor: str = Form(""),
    room: str = Form(""),
    ru_height: int = Form(...),
    notes: str = Form(""),
    face: str = Form(RACK_FACE_FRONT),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_RACKS)
    rack = rd.get_rack(db, rack_id)
    try:
        rd.update_rack(
            db,
            rack,
            name=name,
            floor=floor,
            room=room,
            ru_height=ru_height,
            notes=notes,
        )
        db.commit()
        flash(request, "Rack updated.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/{rack_id}?face={face}", status_code=303)


@router.post("/{rack_id}/delete")
def delete_rack(
    rack_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
):
    rd.require_cap(db, user, RACK_CAP_MANAGE_RACKS)
    rack = rd.get_rack(db, rack_id)
    site_id = rack.site_id
    name = rack.name
    rd.delete_rack(db, rack)
    db.commit()
    flash(request, f"Removed rack {name}.", "ok")
    return RedirectResponse(f"/racks/sites/{site_id}", status_code=303)


@router.post("/{rack_id}/items")
def add_item(
    rack_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    item_type_id: int = Form(...),
    name: str = Form(""),
    ru_start: int = Form(...),
    ru_height: int = Form(0),
    face: str = Form(RACK_FACE_FRONT),
    management_ip: str = Form(""),
    notes: str = Form(""),
):
    rd.require_cap(db, user, RACK_CAP_EDIT_LAYOUT)
    rack = rd.get_rack(db, rack_id)
    try:
        rd.place_item(
            db,
            rack,
            item_type_id=item_type_id,
            name=name,
            ru_start=ru_start,
            ru_height=ru_height or None,
            face=face,
            mount=RACK_MOUNT_RU,
            management_ip=management_ip,
            notes=notes,
        )
        db.commit()
        flash(request, "Item placed.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/{rack_id}?face={face}", status_code=303)


@router.post("/items/{item_id}/move")
def move_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    ru_start: int = Form(...),
    face: str = Form(""),
    side: str = Form(""),
    ru_height: int = Form(0),
    view: str = Form(RACK_FACE_FRONT),
):
    rd.require_cap(db, user, RACK_CAP_EDIT_LAYOUT)
    item = db.get(RackItem, item_id)
    if item is None:
        flash(request, "Item not found.", "error")
        return RedirectResponse("/racks", status_code=303)
    rack_id = item.rack_id
    try:
        rd.move_item(
            db,
            item,
            ru_start=ru_start,
            # Blank keeps the item's own face; the view is only for redirecting.
            face=face or None,
            ru_height=ru_height or None,
            side=side or None,
        )
        db.commit()
        flash(request, "Item moved.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/{rack_id}?face={view}", status_code=303)


@router.post("/items/{item_id}/update")
def update_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    name: str = Form(""),
    management_ip: str = Form(""),
    network_ports: int = Form(0),
    power_ports: int = Form(0),
    notes: str = Form(""),
    face: str = Form(RACK_FACE_FRONT),
):
    rd.require_cap(db, user, RACK_CAP_EDIT_LAYOUT)
    item = db.get(RackItem, item_id)
    if item is None:
        flash(request, "Item not found.", "error")
        return RedirectResponse("/racks", status_code=303)
    item.name = name.strip() or item.name
    item.management_ip = management_ip.strip()
    item.network_ports = max(0, network_ports)
    item.power_ports = max(0, power_ports)
    item.notes = notes.strip()
    db.commit()
    flash(request, "Item updated.", "ok")
    return RedirectResponse(f"/racks/{item.rack_id}?face={face}", status_code=303)


@router.post("/items/{item_id}/delete")
def delete_item(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_user),
    face: str = Form(RACK_FACE_FRONT),
):
    rd.require_cap(db, user, RACK_CAP_EDIT_LAYOUT)
    item = db.get(RackItem, item_id)
    if item is None:
        flash(request, "Item not found.", "error")
        return RedirectResponse("/racks", status_code=303)
    rack_id = item.rack_id
    db.delete(item)
    db.commit()
    flash(request, "Item removed.", "ok")
    return RedirectResponse(f"/racks/{rack_id}?face={face}", status_code=303)
