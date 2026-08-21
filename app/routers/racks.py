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
    )


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
    face: str = Form(RACK_FACE_FRONT),
    ru_height: int = Form(0),
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
            face=face,
            ru_height=ru_height or None,
        )
        db.commit()
        flash(request, "Item moved.", "ok")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        flash(request, str(getattr(exc, "detail", None) or exc), "error")
    return RedirectResponse(f"/racks/{rack_id}?face={face}", status_code=303)


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
