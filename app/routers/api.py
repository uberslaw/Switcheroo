from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.models import REQUEST_BOUNCE, REQUEST_NO_SHUTDOWN, REQUEST_VLAN, Port, User
from app.services.cooldown import CooldownActive
from app.services.request_service import RequestError, create_request
from app.services.switch_service import PermissionDenied, get_switch_for_user, refresh_port
from app.templating import flash, render

router = APIRouter()


def _current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return require_user(db, request)


def _port_or_redirect(db: Session, user: User, switch_id: int, port_id: int, request: Request):
    try:
        get_switch_for_user(db, user, switch_id)
    except PermissionDenied:
        flash(request, "You do not have access to that switch.", "error")
        return None, RedirectResponse("/", status_code=303)
    port = db.get(Port, port_id)
    if port is None or port.switch_id != switch_id:
        flash(request, "Port not found.", "error")
        return None, RedirectResponse(f"/switches/{switch_id}", status_code=303)
    return port, None


def _back(switch_id: int, port_id: int) -> RedirectResponse:
    return RedirectResponse(f"/switches/{switch_id}?port={port_id}", status_code=303)


@router.post("/switches/{switch_id}/ports/{port_id}/refresh")
def on_demand_refresh(
    switch_id: int,
    port_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    port, err = _port_or_redirect(db, user, switch_id, port_id, request)
    if err:
        return err
    try:
        refresh_port(db, port, honor_cooldown=True)
        db.commit()
        flash(request, f"Refreshed {port.if_name} (VLAN/MAC/IP/ISE + status).", "ok")
    except CooldownActive as exc:
        db.rollback()
        flash(request, f"Shared cooldown: try again in {exc.remaining}s. This is per port, not per user.", "error")
    except Exception as exc:  # noqa: BLE001
        db.commit()
        flash(request, f"Refresh failed: {exc}", "error")
    return _back(switch_id, port_id)


@router.post("/switches/{switch_id}/ports/{port_id}/request/vlan")
def request_vlan(
    switch_id: int,
    port_id: int,
    request: Request,
    vlan_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    port, err = _port_or_redirect(db, user, switch_id, port_id, request)
    if err:
        return err
    try:
        req = create_request(db, user, port, REQUEST_VLAN, vlan_id=vlan_id)
        db.commit()
        extra = f" ServiceNow: {req.servicenow_ticket}." if req.servicenow_ticket else ""
        if req.auto_approved:
            title = "VLAN Change Auto-approved"
            detail = f"{port.if_name.replace('GigabitEthernet', 'Gi')} → {vlan_id} {req.requested_vlan_name or ''} ({req.auto_approve_reason}).{extra}"
            queued = f"VLAN change to {vlan_id} auto-approved ({req.auto_approve_reason}).{extra}"
            category = "ok"
        else:
            title = "VLAN Change Requested"
            detail = f"{port.if_name.replace('GigabitEthernet', 'Gi')} → {vlan_id} {req.requested_vlan_name or ''}{extra}"
            queued = f"VLAN change to {vlan_id} queued for Networks approval.{extra}"
            category = "pending"
        if request.headers.get("HX-Request"):
            response = render(
                request,
                "partials/toast.html",
                user=user,
                title=title,
                detail=detail,
                category=category,
            )
            response.headers["HX-Trigger"] = "refreshPaneStatus"
            return response
        flash(request, title, "toast")
        flash(request, queued, "ok")
    except RequestError as exc:
        db.rollback()
        if request.headers.get("HX-Request"):
            return render(
                request,
                "partials/toast.html",
                user=user,
                title="VLAN request failed",
                detail=str(exc),
                category="error",
            )
        flash(request, str(exc), "error")
    return _back(switch_id, port_id)


@router.post("/switches/{switch_id}/ports/{port_id}/request/bounce")
def request_bounce(
    switch_id: int,
    port_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    port, err = _port_or_redirect(db, user, switch_id, port_id, request)
    if err:
        return err
    req = create_request(db, user, port, REQUEST_BOUNCE)
    db.commit()
    extra = f" ServiceNow: {req.servicenow_ticket}." if req.servicenow_ticket else ""
    if req.auto_approved:
        flash(request, f"Port bounce auto-approved ({req.auto_approve_reason}).{extra}", "ok")
    else:
        flash(request, f"Port bounce queued for Networks approval.{extra}", "ok")
    return _back(switch_id, port_id)


@router.post("/switches/{switch_id}/ports/{port_id}/request/no-shutdown")
def request_noshut(
    switch_id: int,
    port_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(_current_user),
):
    port, err = _port_or_redirect(db, user, switch_id, port_id, request)
    if err:
        return err
    req = create_request(db, user, port, REQUEST_NO_SHUTDOWN)
    db.commit()
    extra = f" ServiceNow: {req.servicenow_ticket}." if req.servicenow_ticket else ""
    flash(request, f"Bring-online request queued for Networks approval.{extra}", "ok")
    return _back(switch_id, port_id)
