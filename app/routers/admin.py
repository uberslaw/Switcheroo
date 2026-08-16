from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth import hash_password, password_meets_policy, require_networks, require_user, safe_next_path
from app.crypto import store_secret
from app.db import get_db
from app.drivers.simulator import simulator
from app.models import (
    PORT_PURPOSES,
    ROLE_CS,
    ROLE_NETWORKS,
    STATUS_PENDING,
    ChangeRequest,
    Port,
    Switch,
    SwitchVlan,
    User,
    UserSwitchPermission,
)
from app.services.auto_approve import global_key, is_enabled, office_key, requestor_key, set_policy
from app.services.request_service import RequestError, approve_request, reject_request
from app.templating import flash, render

router = APIRouter(prefix="/admin")


def _safe_next(raw: str | None, default: str = "/admin/approvals") -> str:
    """Allow only same-origin relative paths so approve/reject can return to /requests."""
    return safe_next_path(raw, default)


def _admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_user(db, request)
    return require_networks(user)


@router.get("/inventory")
def inventory(request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    switches = list(db.scalars(select(Switch).order_by(Switch.name)).all())
    return render(request, "admin/inventory.html", user=user, switches=switches)


@router.get("/switches/new")
def switch_new(request: Request, user: User = Depends(_admin)):
    return render(request, "admin/switch_form.html", user=user, switch=None)


@router.post("/switches/new")
def switch_create(
    request: Request,
    name: str = Form(...),
    management_ip: str = Form(""),
    location: str = Form(""),
    notes: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    driver_override: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    existing = db.scalar(select(Switch).where(Switch.name == name.strip()))
    if existing:
        flash(request, "A switch with that name already exists.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    override = driver_override.strip() or None
    switch = Switch(
        name=name.strip(),
        management_ip=management_ip.strip(),
        location=location.strip(),
        notes=notes.strip(),
        username=username.strip() or None,
        password=store_secret(password) if password else None,
        driver_override=override,
    )
    db.add(switch)
    db.commit()
    flash(request, f"Added {switch.name}. Edit ports to assign purposes.", "ok")
    return RedirectResponse(f"/admin/switches/{switch.id}/ports", status_code=303)


@router.get("/switches/{switch_id}/edit")
def switch_edit(switch_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    switch = db.get(Switch, switch_id)
    if switch is None:
        flash(request, "Switch not found.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    return render(request, "admin/switch_form.html", user=user, switch=switch)


@router.post("/switches/{switch_id}/edit")
def switch_update(
    switch_id: int,
    request: Request,
    name: str = Form(...),
    management_ip: str = Form(""),
    location: str = Form(""),
    notes: str = Form(""),
    username: str = Form(""),
    password: str = Form(""),
    driver_override: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    switch = db.get(Switch, switch_id)
    if switch is None:
        flash(request, "Switch not found.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    switch.name = name.strip()
    switch.management_ip = management_ip.strip()
    switch.location = location.strip()
    switch.notes = notes.strip()
    if username.strip():
        switch.username = username.strip()
    if password:
        switch.password = store_secret(password)
    switch.driver_override = driver_override.strip() or None
    db.commit()
    flash(request, "Switch updated.", "ok")
    return RedirectResponse("/admin/inventory", status_code=303)


@router.get("/switches/{switch_id}/ports")
def port_editor(switch_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    switch = db.get(Switch, switch_id)
    if switch is None:
        flash(request, "Switch not found.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    ports = list(db.scalars(select(Port).where(Port.switch_id == switch.id).order_by(Port.if_index)).all())
    return render(
        request,
        "admin/ports.html",
        user=user,
        switch=switch,
        ports=ports,
        purposes=PORT_PURPOSES,
    )


@router.post("/switches/{switch_id}/ports/{port_id}")
def port_update(
    switch_id: int,
    port_id: int,
    request: Request,
    purpose: str = Form(...),
    friendly_label: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    port = db.get(Port, port_id)
    if port is None or port.switch_id != switch_id:
        flash(request, "Port not found.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    if purpose not in PORT_PURPOSES:
        flash(request, "Invalid purpose.", "error")
        return RedirectResponse(f"/admin/switches/{switch_id}/ports", status_code=303)
    port.purpose = purpose
    port.friendly_label = friendly_label.strip()
    db.commit()
    flash(request, f"Updated {port.if_name}.", "ok")
    target = next.strip() if next.strip().startswith("/") else f"/admin/switches/{switch_id}/ports"
    return RedirectResponse(target, status_code=303)


@router.post("/switches/{switch_id}/ports/add")
def port_add(
    switch_id: int,
    request: Request,
    if_name: str = Form(...),
    if_index: int = Form(...),
    purpose: str = Form("unused"),
    friendly_label: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    switch = db.get(Switch, switch_id)
    if switch is None:
        flash(request, "Switch not found.", "error")
        return RedirectResponse("/admin/inventory", status_code=303)
    existing = db.scalar(select(Port).where(Port.switch_id == switch_id, Port.if_name == if_name.strip()))
    if existing:
        flash(request, "That interface already exists on this switch.", "error")
        return RedirectResponse(f"/admin/switches/{switch_id}/ports", status_code=303)
    port = Port(
        switch_id=switch_id,
        if_name=if_name.strip(),
        if_index=if_index,
        purpose=purpose if purpose in PORT_PURPOSES else "unused",
        friendly_label=friendly_label.strip(),
        oper_status="down",
        admin_status="up",
    )
    db.add(port)
    db.flush()
    simulator.hydrate_from_port(switch, port)
    db.commit()
    flash(request, f"Added {port.if_name}.", "ok")
    return RedirectResponse(f"/admin/switches/{switch_id}/ports", status_code=303)


@router.post("/switches/{switch_id}/vlans")
def vlan_add(
    switch_id: int,
    request: Request,
    vlan_id: int = Form(...),
    vlan_name: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    existing = db.scalar(select(SwitchVlan).where(SwitchVlan.switch_id == switch_id, SwitchVlan.vlan_id == vlan_id))
    if existing:
        existing.vlan_name = vlan_name.strip()
    else:
        db.add(SwitchVlan(switch_id=switch_id, vlan_id=vlan_id, vlan_name=vlan_name.strip()))
    db.commit()
    flash(request, f"VLAN {vlan_id} saved.", "ok")
    return RedirectResponse(f"/admin/switches/{switch_id}/ports", status_code=303)


@router.get("/approvals")
def approvals(request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    pending = list(
        db.scalars(
            select(ChangeRequest)
            .where(ChangeRequest.status == STATUS_PENDING)
            .order_by(ChangeRequest.created_at.desc())
        ).all()
    )
    return render(request, "admin/approvals.html", user=user, pending=pending)


@router.post("/approvals/{request_id}/approve")
def approval_approve(
    request_id: int,
    request: Request,
    note: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    dest = _safe_next(next)
    req = db.get(ChangeRequest, request_id)
    if req is None:
        flash(request, "Request not found.", "error")
        return RedirectResponse(dest, status_code=303)
    try:
        approve_request(db, req, user, note)
        db.commit()
        if req.status == "executed":
            flash(request, f"Approved and executed request #{req.id}.", "ok")
        else:
            flash(request, f"Approved but execution failed: {req.error_message}", "error")
    except RequestError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(dest, status_code=303)


@router.post("/approvals/{request_id}/reject")
def approval_reject(
    request_id: int,
    request: Request,
    note: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    dest = _safe_next(next)
    req = db.get(ChangeRequest, request_id)
    if req is None:
        flash(request, "Request not found.", "error")
        return RedirectResponse(dest, status_code=303)
    try:
        reject_request(db, req, user, note)
        db.commit()
        flash(request, f"Rejected request #{req.id}.", "ok")
    except RequestError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(dest, status_code=303)


@router.get("/policies")
def policies_page(request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    switches = list(db.scalars(select(Switch).order_by(Switch.location, Switch.name)).all())
    offices = sorted({s.location for s in switches if (s.location or "").strip()})
    cs_users = list(db.scalars(select(User).where(User.role == ROLE_CS).order_by(User.username)).all())
    office_rows = [{"location": loc, "enabled": is_enabled(db, office_key(loc))} for loc in offices]
    user_rows = [{"user": u, "enabled": is_enabled(db, requestor_key(u.id))} for u in cs_users]
    return render(
        request,
        "admin/policies.html",
        user=user,
        global_on=is_enabled(db, global_key()),
        office_rows=office_rows,
        user_rows=user_rows,
    )


@router.post("/policies/global")
def policies_global(
    request: Request,
    enabled: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    set_policy(db, global_key(), enabled == "1")
    db.commit()
    flash(request, "Everywhere auto-approve is on." if enabled == "1" else "Everywhere auto-approve is off.", "ok")
    return RedirectResponse("/admin/policies", status_code=303)


@router.post("/policies/office")
def policies_office(
    request: Request,
    office: str = Form(...),
    enabled: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    location = office.strip()
    if not location:
        flash(request, "Office / location is required.", "error")
        return RedirectResponse("/admin/policies", status_code=303)
    set_policy(db, office_key(location), enabled == "1")
    db.commit()
    state = "on" if enabled == "1" else "off"
    flash(request, f"Office auto-approve {state} for {location}.", "ok")
    return RedirectResponse("/admin/policies", status_code=303)


@router.post("/policies/requestor")
def policies_requestor(
    request: Request,
    user_id: int = Form(...),
    enabled: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    target = db.get(User, user_id)
    if target is None or target.role != ROLE_CS:
        flash(request, "Client Services user not found.", "error")
        return RedirectResponse("/admin/policies", status_code=303)
    set_policy(db, requestor_key(target.id), enabled == "1")
    db.commit()
    state = "on" if enabled == "1" else "off"
    flash(request, f"Requestor auto-approve {state} for {target.username}.", "ok")
    return RedirectResponse("/admin/policies", status_code=303)


@router.get("/history")
def history(request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    rows = list(db.scalars(select(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(200)).all())
    return render(request, "admin/history.html", user=user, rows=rows)


@router.get("/permissions")
def permissions(request: Request, db: Session = Depends(get_db), user: User = Depends(_admin)):
    users = list(db.scalars(select(User).order_by(User.username)).all())
    switches = list(db.scalars(select(Switch).order_by(Switch.name)).all())
    links = {(p.user_id, p.switch_id) for p in db.scalars(select(UserSwitchPermission)).all()}
    return render(
        request,
        "admin/permissions.html",
        user=user,
        users=users,
        switches=switches,
        links=links,
        roles=(ROLE_CS, ROLE_NETWORKS),
    )


@router.post("/permissions")
def permission_set(
    request: Request,
    user_id: int = Form(...),
    switch_id: int = Form(...),
    granted: str = Form("0"),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    target = db.get(User, user_id)
    switch = db.get(Switch, switch_id)
    if target is None or switch is None:
        flash(request, "User or switch not found.", "error")
        return RedirectResponse("/admin/permissions", status_code=303)
    link = db.scalar(
        select(UserSwitchPermission).where(
            UserSwitchPermission.user_id == user_id,
            UserSwitchPermission.switch_id == switch_id,
        )
    )
    if granted == "1" and link is None:
        db.add(UserSwitchPermission(user_id=user_id, switch_id=switch_id))
        flash(request, f"Granted {target.username} access to {switch.name}.", "ok")
    elif granted != "1" and link is not None:
        db.delete(link)
        flash(request, f"Revoked {target.username} access to {switch.name}.", "ok")
    db.commit()
    return RedirectResponse("/admin/permissions", status_code=303)


@router.post("/users")
def user_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    display_name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(_admin),
):
    if role not in {ROLE_CS, ROLE_NETWORKS}:
        flash(request, "Role must be cs or networks.", "error")
        return RedirectResponse("/admin/permissions", status_code=303)
    if db.scalar(select(User).where(User.username == username.strip())):
        flash(request, "Username already exists.", "error")
        return RedirectResponse("/admin/permissions", status_code=303)
    if not password_meets_policy(password):
        flash(request, "Password must be at least 10 characters.", "error")
        return RedirectResponse("/admin/permissions", status_code=303)
    db.add(
        User(
            username=username.strip(),
            password_hash=hash_password(password),
            role=role,
            display_name=display_name.strip() or username.strip(),
        )
    )
    db.commit()
    audit("user_created", username=username.strip()[:64], role=role, actor=user.username)
    flash(request, f"Created user {username.strip()}.", "ok")
    return RedirectResponse("/admin/permissions", status_code=303)
