from __future__ import annotations

from datetime import datetime, timedelta

from collections import defaultdict

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
from app.services.office import build_office_views, find_office
from app.services.patching import (
    available_ports_on_switches,
    find_outlet,
    find_patching_stack,
    jack_outlets,
    outlet_with_patch,
    panels_for_location,
    patch_for_port,
    patched_switch_port_ids,
    patching_bays,
    patching_stacks,
    search_outlets,
    stack_for_outlet,
    stack_key,
)
from app.services.request_service import pending_vlan_request
from app.services.switch_service import (
    PermissionDenied,
    TroubleshootConflict,
    active_session_for_user,
    get_switch_for_user,
    start_troubleshooting,
    stop_troubleshooting,
    visible_switches,
)
from app.services.uptime import faceplate_groups, port_led, problem_reason
from app.templating import flash, render

router = APIRouter()


def _optional_int(value: str | int | None) -> int | None:
    if value is None or value is False:
        return None
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if not raw.isdigit():
        return None
    return int(raw)


def _current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return require_user(db, request)


def _office_members(office) -> list[Switch]:
    members: list[Switch] = list(office.unstacked)
    for stack in office.floor_stacks:
        members.extend(stack.members)
    if office.mcr is not None:
        for stack in office.mcr.stacks:
            members.extend(stack.members)
    for room in office.other_rooms:
        for stack in room.stacks:
            members.extend(stack.members)
    seen: set[int] = set()
    unique: list[Switch] = []
    for switch in members:
        if switch.id in seen:
            continue
        seen.add(switch.id)
        unique.append(switch)
    return unique


def _dashboard_cards(db: Session, switches: list[Switch]) -> dict[int, dict]:
    ids = [s.id for s in switches]
    ports_by_switch: dict[int, list[Port]] = defaultdict(list)
    if ids:
        ports = list(
            db.scalars(select(Port).where(Port.switch_id.in_(ids)).order_by(Port.if_index)).all()
        )
        for port in ports:
            ports_by_switch[port.switch_id].append(port)
    cards: dict[int, dict] = {}
    for switch in switches:
        ports = ports_by_switch.get(switch.id, [])
        up = sum(1 for p in ports if p.oper_status == "up")
        down = sum(1 for p in ports if p.oper_status != "up")
        shut = sum(1 for p in ports if p.admin_status == "down")
        leds = [port_led(p) for p in ports]
        while len(leds) < 48:
            leds.append("dark-green")
        cards[switch.id] = {
            "switch": switch,
            "counts": {"total": len(ports), "up": up, "down": down, "shutdown": shut},
            "leds": leds[:48],
        }
    return cards


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
    card_by_id = _dashboard_cards(db, switches)
    offices = build_office_views(switches)
    pending = 0
    if user.role == ROLE_NETWORKS:
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
    session = active_session_for_user(db, user.id)
    return render(
        request,
        "dashboard.html",
        user=user,
        offices=offices,
        card_by_id=card_by_id,
        has_stacked=any(office.stacked for office in offices),
        pending=pending,
        session=session,
        interval=get_settings().status_poll_interval,
        patching_view=False,
    )


@router.get("/offices/{slug}")
def office_page(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    view: str | None = None,
    fo: str | None = None,
    q: str | None = None,
    switch: str | None = None,
    show_available: str | None = None,
    stack: str | None = None,
    port: str | None = None,
    pick_stack: str | None = None,
):
    switches = visible_switches(db, user)
    offices = build_office_views(switches)
    office = find_office(offices, slug)
    if office is None:
        flash(request, "That office is not visible to your account.", "error")
        return RedirectResponse("/", status_code=303)
    members = _office_members(office)
    card_by_id = _dashboard_cards(db, members)
    pending = 0
    if user.role == ROLE_NETWORKS:
        pending = db.scalar(select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending")) or 0
    session = active_session_for_user(db, user.id)
    patching_view = (view or "").lower() == "patching"
    switch_id = _optional_int(switch)
    ctx = {
        "office": office,
        "card_by_id": card_by_id,
        "pending": pending,
        "session": session,
        "interval": get_settings().status_poll_interval,
        "patching_view": patching_view,
    }
    if patching_view:
        ctx.update(
            _patching_office_ctx(
                db,
                office,
                members,
                fo=fo,
                q=q,
                switch_id=switch_id,
                show_available=bool(_optional_int(show_available)) or bool(switch_id),
                stack_name=stack,
                port_id=_optional_int(port),
                pick_stack=pick_stack,
            )
        )
    return render(request, "office.html", user=user, **ctx)


def _office_or_none(db: Session, user: User, slug: str):
    switches = visible_switches(db, user)
    offices = build_office_views(switches)
    office = find_office(offices, slug)
    if office is None:
        return None, []
    return office, _office_members(office)


@router.get("/partials/offices/{slug}/fo-pane")
def fo_pane_partial(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    fo: str | None = None,
    q: str | None = None,
    switch: str | None = None,
    show_available: str | None = None,
    stack: str | None = None,
    port: str | None = None,
    pick_stack: str | None = None,
):
    office, members = _office_or_none(db, user, slug)
    if office is None:
        return render(request, "partials/denied.html", user=user)
    ctx = _patching_office_ctx(
        db,
        office,
        members,
        fo=fo,
        q=q,
        switch_id=_optional_int(switch),
        show_available=bool(_optional_int(show_available)) or bool(switch),
        stack_name=stack,
        port_id=_optional_int(port),
        pick_stack=pick_stack,
    )
    ctx["office"] = office
    return render(request, "partials/fo_pane.html", user=user, **ctx)


@router.get("/partials/offices/{slug}/fo-suggest")
def fo_suggest_partial(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    q: str | None = None,
    fo: str | None = None,
):
    office, _members = _office_or_none(db, user, slug)
    if office is None:
        return render(request, "partials/denied.html", user=user)
    hits = search_outlets(db, office.name, q or fo or "")
    return render(
        request,
        "partials/fo_suggest.html",
        user=user,
        office=office,
        hits=hits,
        fo_query=q or fo or "",
    )


@router.get("/partials/offices/{slug}/available")
def available_partial(
    slug: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
    fo: str | None = None,
    switch: str | None = None,
    stack: str | None = None,
    pick_stack: str | None = None,
    show_available: str | None = None,
    port: str | None = None,
):
    office, members = _office_or_none(db, user, slug)
    if office is None:
        return render(request, "partials/denied.html", user=user)
    ctx = _patching_office_ctx(
        db,
        office,
        members,
        fo=fo,
        q=None,
        switch_id=_optional_int(switch),
        show_available=True,
        stack_name=stack or pick_stack,
        port_id=_optional_int(port),
        pick_stack=pick_stack or stack,
    )
    ctx["office"] = office
    ctx["card_by_id"] = _dashboard_cards(db, members)
    return render(request, "partials/available_ports.html", user=user, **ctx)


def _patching_office_ctx(
    db: Session,
    office,
    members: list[Switch],
    *,
    fo: str | None,
    q: str | None,
    switch_id: int | None,
    show_available: bool,
    stack_name: str | None = None,
    port_id: int | None = None,
    pick_stack: str | None = None,
) -> dict:
    panels = panels_for_location(db, office.name)
    query = (fo or q or "").strip()
    selected_fo = find_outlet(db, query) if query else None
    if selected_fo is not None:
        selected_fo = outlet_with_patch(db, selected_fo.id) or selected_fo

    switch_port: Port | None = None
    fo_patch = selected_fo.patch if selected_fo is not None else None
    if port_id:
        switch_port = db.get(Port, port_id)
        if switch_port is not None:
            port_patch = patch_for_port(db, switch_port.id)
            if port_patch is not None:
                fo_patch = port_patch
                selected_fo = outlet_with_patch(db, port_patch.field_outlet_id) or port_patch.field_outlet
    elif fo_patch is not None:
        switch_port = fo_patch.port

    selected_view = None
    if switch_port is not None:
        selected_view = _port_view(switch_port)
        selected_view["pending_vlan"] = pending_vlan_request(db, switch_port.id)

    inferred = stack_for_outlet(office, selected_fo, panels)
    if switch_port is not None and switch_port.switch is not None:
        inferred = find_patching_stack(office, switch_port.switch.stack_name) or inferred
    active_stack = find_patching_stack(office, stack_name) if stack_name else inferred
    picker_stack = find_patching_stack(office, pick_stack) if pick_stack else active_stack

    stacks = patching_stacks(office)
    all_bays = patching_bays(office, panels)
    active_key = stack_key(active_stack) if active_stack is not None else ""
    patch_bays = [bay for bay in all_bays if stack_key(bay["stack"]) == active_key] or all_bays[:1]

    patched_ids = patched_switch_port_ids(db)
    available: list[Port] = []
    available_ids: set[int] = set()
    if show_available and active_stack is not None:
        available = available_ports_on_switches(db, active_stack.members)
        available_ids = {p.id for p in available}

    highlight_switch = None
    highlight_ports: list[Port] = []
    if switch_id and picker_stack is not None:
        highlight_switch = next((s for s in picker_stack.members if s.id == switch_id), None)
    if highlight_switch is not None:
        highlight_ports = list(
            db.scalars(
                select(Port).where(Port.switch_id == highlight_switch.id).order_by(Port.if_index)
            ).all()
        )

    for bay in patch_bays:
        for row in bay["rows"]:
            sw = row["switch"]
            ports = list(
                db.scalars(select(Port).where(Port.switch_id == sw.id).order_by(Port.if_index)).all()
            )
            row["groups"] = faceplate_groups(ports)
            row["views"] = {p.id: _port_view(p) for p in ports}
            row["selected"] = switch_port if switch_port is not None and switch_port.switch_id == sw.id else None

    picker_switches = list(picker_stack.members) if picker_stack is not None else []

    return {
        "panels": panels,
        "patch_bays": patch_bays,
        "patching_stacks": stacks,
        "active_stack": active_stack,
        "active_stack_key": active_key,
        "picker_stack": picker_stack,
        "picker_stack_key": stack_key(picker_stack) if picker_stack is not None else "",
        "picker_switches": picker_switches,
        "jack_fos": jack_outlets(db, panels),
        "fo_query": query,
        "selected_fo": selected_fo,
        "fo_patch": fo_patch,
        "fo_switch_port": switch_port,
        "selected": switch_port,
        "selected_view": selected_view,
        "switch": switch_port.switch if switch_port is not None else None,
        "office_switches": members,
        "show_available": show_available,
        "highlight_switch": highlight_switch,
        "highlight_ports": highlight_ports,
        "available_ports": available,
        "available_ids": available_ids,
        "patched_ids": patched_ids,
        "patching_mode": True,
    }


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


@router.get("/help")
def help_page(request: Request, db: Session = Depends(get_db), user: User = Depends(_current_user)):
    settings = get_settings()
    return render(
        request,
        "help.html",
        user=user,
        interval=settings.status_poll_interval,
        cooldown=settings.on_demand_cooldown,
        data_dir=settings.data_dir,
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
