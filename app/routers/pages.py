from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import authenticate, require_user, user_from_request
from app.config import get_settings
from app.db import get_db
from app.models import (
    PORT_PURPOSES,
    ROLE_NETWORKS,
    ChangeRequest,
    Port,
    Switch,
    TroubleshootingSession,
    User,
    utcnow,
)
from app.services.cooldown import remaining_seconds
from app.services.export import build_ports_workbook
from app.services.switch_service import (
    PermissionDenied,
    TroubleshootConflict,
    active_session_for_user,
    get_switch_for_user,
    start_troubleshooting,
    stop_troubleshooting,
    visible_switches,
)
from app.services.request_service import pending_vlan_request
from app.services.uptime import faceplate_groups, port_led, problem_reason
from app.templating import flash, render

router = APIRouter()


def _current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return require_user(db, request)


def _switch_counts(db: Session, switch: Switch) -> dict:
    ports = list(db.scalars(select(Port).where(Port.switch_id == switch.id)).all())
    up = sum(1 for p in ports if p.oper_status == "up")
    down = sum(1 for p in ports if p.oper_status != "up")
    shut = sum(1 for p in ports if p.admin_status == "down")
    return {"total": len(ports), "up": up, "down": down, "shutdown": shut}


def _port_view(port: Port) -> dict:
    ok = remaining_seconds(port.last_on_demand_at) == 0
    left = remaining_seconds(port.last_on_demand_at)
    return {
        "port": port,
        "can_refresh": ok,
        "cooldown": left,
        "led": port_led(port),
        "problem": problem_reason(port),
        "connected": port.link_up_since,
        "pending_vlan": None,
    }


def _workspace_context(db: Session, user: User, switch: Switch, selected_id: int | None) -> dict:
    ports = list(db.scalars(select(Port).where(Port.switch_id == switch.id).order_by(Port.if_index)).all())
    selected = None
    if selected_id:
        selected = next((p for p in ports if p.id == selected_id), None)
    session = active_session_for_user(db, user.id)
    views = {p.id: _port_view(p) for p in ports}
    if selected is not None:
        views[selected.id]["pending_vlan"] = pending_vlan_request(db, selected.id)
    return {
        "switch": switch,
        "ports": ports,
        "groups": faceplate_groups(ports),
        "views": views,
        "selected": selected,
        "selected_view": views.get(selected.id) if selected else None,
        "vlans": list(switch.vlans),
        "session": session,
        "purposes": PORT_PURPOSES,
        "now": utcnow(),
    }


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if user_from_request(db, request):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", user=None)


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate(db, username.strip(), password)
    if user is None:
        flash(request, "Unknown user or bad password.", "error")
        return render(request, "login.html", user=None, status_code=401)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(_current_user)):
    switches = visible_switches(db, user)
    cards = []
    settings = get_settings()
    for switch in switches:
        cards.append({"switch": switch, "counts": _switch_counts(db, switch)})
    pending = 0
    if user.role == ROLE_NETWORKS:
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
    session = active_session_for_user(db, user.id)
    return render(
        request,
        "dashboard.html",
        user=user,
        cards=cards,
        pending=pending,
        session=session,
        interval=settings.status_poll_interval,
    )


@router.get("/switches/{switch_id}")
def switch_detail(
    switch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    port: int | None = None,
):
    try:
        switch = get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        flash(request, "You do not have access to that switch.", "error")
        return RedirectResponse("/", status_code=303)
    ctx = _workspace_context(db, user, switch, port)
    return render(request, "switch_detail.html", user=user, **ctx)


@router.get("/partials/switches/{switch_id}/workspace")
def workspace_partial(
    switch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    port: int | None = None,
):
    try:
        switch = get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        return render(request, "partials/denied.html", user=user)
    ctx = _workspace_context(db, user, switch, port)
    return render(request, "partials/workspace.html", user=user, **ctx)


@router.get("/partials/switches/{switch_id}/faceplate")
def faceplate_partial(
    switch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    port: int | None = None,
):
    try:
        switch = get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        return render(request, "partials/denied.html", user=user)
    ctx = _workspace_context(db, user, switch, port)
    return render(request, "partials/faceplate.html", user=user, **ctx)


@router.get("/partials/switches/{switch_id}/pane-status")
def pane_status_partial(
    switch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    port: int | None = None,
):
    try:
        switch = get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        return render(request, "partials/denied.html", user=user)
    ctx = _workspace_context(db, user, switch, port)
    if ctx["selected"] is None:
        return render(request, "partials/denied.html", user=user)
    return render(request, "partials/pane_status.html", user=user, **ctx)


@router.get("/partials/troubleshoot")
def troubleshoot_partial(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    session = active_session_for_user(db, user.id)
    remaining = 0
    if session is not None:
        remaining = max(0, int((session.ends_at - utcnow()).total_seconds()))
    return render(
        request,
        "partials/troubleshoot_banner.html",
        user=user,
        session=session,
        remaining=remaining,
    )


@router.post("/switches/{switch_id}/ports/{port_id}/troubleshoot")
def start_ts(
    switch_id: int,
    port_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    try:
        get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        flash(request, "You do not have access to that switch.", "error")
        return RedirectResponse("/", status_code=303)
    port = db.get(Port, port_id)
    if port is None or port.switch_id != switch_id:
        flash(request, "Port not found.", "error")
        return RedirectResponse(f"/switches/{switch_id}", status_code=303)
    try:
        start_troubleshooting(db, user, port)
        db.commit()
        flash(request, f"Troubleshooting {port.if_name} for 5 minutes (10s polls, this port only).", "ok")
    except TroubleshootConflict as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/switches/{switch_id}?port={port_id}", status_code=303)


@router.post("/troubleshoot/{session_id}/stop")
def stop_ts(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    session = db.get(TroubleshootingSession, session_id)
    if session is None or session.user_id != user.id:
        flash(request, "Session not found.", "error")
        return RedirectResponse("/", status_code=303)
    stop_troubleshooting(session)
    db.commit()
    flash(request, "Troubleshooting session stopped.", "ok")
    return RedirectResponse(f"/switches/{session.switch_id}?port={session.port_id}", status_code=303)


@router.get("/switches/{switch_id}/export.xlsx")
def export_switch_xlsx(
    switch_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    try:
        switch = get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        flash(request, "You do not have access to that switch.", "error")
        return RedirectResponse("/", status_code=303)
    payload = build_ports_workbook(db, [switch])
    filename = f"switcheroo-{switch.name}-ports.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/requests")
def review_requests(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    status: str = "",
    switch_id: int | None = None,
    office: str = "",
    date_from: str = "",
    date_to: str = "",
):
    switches = visible_switches(db, user)
    switch_ids = [s.id for s in switches]
    offices = sorted({s.location for s in switches if s.location})
    query = select(ChangeRequest).order_by(ChangeRequest.created_at.desc())
    if user.role != ROLE_NETWORKS:
        if not switch_ids:
            query = query.where(ChangeRequest.id == -1)
        else:
            query = query.where(ChangeRequest.switch_id.in_(switch_ids))
    if status:
        if status == "auto":
            query = query.where(ChangeRequest.auto_approved.is_(True))
        else:
            wanted = [status]
            if status == "approved":
                wanted = ["approved", "executed"]
            if status == "denied":
                wanted = ["rejected"]
            query = query.where(ChangeRequest.status.in_(wanted))
    if switch_id:
        if user.role != ROLE_NETWORKS and switch_id not in switch_ids:
            flash(request, "You do not have access to that switch.", "error")
            return RedirectResponse("/requests", status_code=303)
        query = query.where(ChangeRequest.switch_id == switch_id)
    if office:
        office_ids = [s.id for s in switches if s.location == office]
        if user.role == ROLE_NETWORKS:
            office_ids = [
                s.id
                for s in db.scalars(select(Switch).where(Switch.location == office)).all()
            ]
        query = query.where(ChangeRequest.switch_id.in_(office_ids or [-1]))
    if date_from:
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.where(ChangeRequest.created_at >= start)
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
            query = query.where(ChangeRequest.created_at < end)
        except ValueError:
            pass
    rows = list(db.scalars(query.limit(500)).all())
    all_switches = list(db.scalars(select(Switch).order_by(Switch.name)).all()) if user.role == ROLE_NETWORKS else switches
    return render(
        request,
        "requests.html",
        user=user,
        rows=rows,
        switches=all_switches,
        offices=offices if user.role != ROLE_NETWORKS else sorted({s.location for s in all_switches if s.location}),
        filters={"status": status, "switch_id": switch_id or "", "office": office, "date_from": date_from, "date_to": date_to},
    )


@router.get("/export.xlsx")
def export_visible_xlsx(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    switches = visible_switches(db, user)
    payload = build_ports_workbook(db, switches)
    filename = "switcheroo-visible-switches.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
