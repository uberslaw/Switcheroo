from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.drivers.factory import get_driver
from app.drivers.servicenow import ServiceNowError, servicenow
from app.drivers.teams import TeamsError, teams
from app.models import (
    REQUEST_BOUNCE,
    REQUEST_NO_SHUTDOWN,
    REQUEST_VLAN,
    SOURCE_WRITE,
    STATUS_APPROVED,
    STATUS_EXECUTED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_REJECTED,
    ChangeRequest,
    Port,
    SwitchVlan,
    User,
    utcnow,
)
from app.services.auto_approve import match_auto_approve
from app.services.switch_service import apply_details

log = logging.getLogger("switcheroo.requests")


class RequestError(Exception):
    pass


def pending_vlan_request(db: Session, port_id: int) -> Optional[ChangeRequest]:
    return db.scalar(
        select(ChangeRequest)
        .where(
            ChangeRequest.port_id == port_id,
            ChangeRequest.request_type == REQUEST_VLAN,
            ChangeRequest.status == STATUS_PENDING,
        )
        .order_by(ChangeRequest.created_at.desc())
    )


def create_request(
    db: Session,
    requester: User,
    port: Port,
    request_type: str,
    vlan_id: Optional[int] = None,
) -> ChangeRequest:
    if request_type not in {REQUEST_VLAN, REQUEST_BOUNCE, REQUEST_NO_SHUTDOWN}:
        raise RequestError(f"Unknown request type: {request_type}")
    vlan_name = None
    if request_type == REQUEST_VLAN:
        if vlan_id is None:
            raise RequestError("VLAN change requires a VLAN number")
        catalog = db.scalar(
            select(SwitchVlan).where(SwitchVlan.switch_id == port.switch_id, SwitchVlan.vlan_id == vlan_id)
        )
        vlan_name = catalog.vlan_name if catalog else None
    req = ChangeRequest(
        requester_id=requester.id,
        switch_id=port.switch_id,
        port_id=port.id,
        request_type=request_type,
        requested_vlan_id=vlan_id,
        requested_vlan_name=vlan_name,
        from_vlan_id=port.vlan_id if request_type == REQUEST_VLAN else None,
        from_vlan_name=port.vlan_name if request_type == REQUEST_VLAN else None,
        status=STATUS_PENDING,
    )
    db.add(req)
    db.flush()
    if request_type == REQUEST_VLAN:
        ticket, note = servicenow.create_ticket(req)
        req.servicenow_ticket = ticket
        req.servicenow_note = note
    match = match_auto_approve(db, req)
    if match is not None:
        approve_request(db, req, reviewer=None, note=match.work_notes, auto_reason=match.label)
    elif request_type == REQUEST_VLAN:
        _teams_notify_pending(req)
    return req


def execute_request(db: Session, req: ChangeRequest) -> None:
    port = db.get(Port, req.port_id)
    if port is None:
        req.status = STATUS_FAILED
        req.error_message = "Port no longer exists"
        return
    switch = port.switch
    driver = get_driver(switch)
    try:
        if req.request_type == REQUEST_VLAN:
            if req.requested_vlan_id is None:
                raise RequestError("Missing VLAN id")
            driver.set_access_vlan(switch, port.if_name, req.requested_vlan_id, req.requested_vlan_name or "")
            port.vlan_id = req.requested_vlan_id
            port.vlan_name = req.requested_vlan_name
            port.data_source = SOURCE_WRITE
        elif req.request_type == REQUEST_BOUNCE:
            driver.bounce_port(switch, port.if_name)
            details = driver.poll_interface_details(switch, port.if_name)
            apply_details(port, details, SOURCE_WRITE)
        elif req.request_type == REQUEST_NO_SHUTDOWN:
            driver.no_shutdown(switch, port.if_name)
            details = driver.poll_interface_details(switch, port.if_name)
            apply_details(port, details, SOURCE_WRITE)
        else:
            raise RequestError(f"Unsupported type {req.request_type}")
        req.status = STATUS_EXECUTED
        req.executed_at = utcnow()
        req.error_message = None
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to execute request %s", req.id)
        req.status = STATUS_FAILED
        req.error_message = str(exc)
        req.executed_at = utcnow()


def approve_request(
    db: Session,
    req: ChangeRequest,
    reviewer: User | None,
    note: str = "",
    *,
    auto_reason: str | None = None,
) -> ChangeRequest:
    if req.status != STATUS_PENDING:
        raise RequestError(f"Request {req.id} is {req.status}, not pending")
    req.status = STATUS_APPROVED
    req.reviewer_id = reviewer.id if reviewer is not None else None
    req.review_note = note or None
    req.reviewed_at = utcnow()
    req.auto_approved = bool(auto_reason)
    req.auto_approve_reason = auto_reason
    execute_request(db, req)
    _sn_after_decision(req, approved=True)
    return req


def reject_request(db: Session, req: ChangeRequest, reviewer: User, note: str) -> ChangeRequest:
    if req.status != STATUS_PENDING:
        raise RequestError(f"Request {req.id} is {req.status}, not pending")
    if not (note or "").strip():
        raise RequestError("A reject note is required")
    req.status = STATUS_REJECTED
    req.reviewer_id = reviewer.id
    req.review_note = note.strip()
    req.reviewed_at = utcnow()
    _sn_after_decision(req, approved=False)
    return req


def acknowledge_request(db: Session, req: ChangeRequest, user: User) -> ChangeRequest:
    """Claim a pending request so the rest of Networks does not double up."""
    if req.status != STATUS_PENDING:
        raise RequestError(f"Request {req.id} is {req.status}, not pending")
    if req.acknowledged_by_id is not None:
        if req.acknowledged_by_id == user.id:
            return req
        who = req.acknowledged_by.username if req.acknowledged_by is not None else str(req.acknowledged_by_id)
        raise RequestError(f"Already acknowledged by {who}")
    req.acknowledged_by_id = user.id
    req.acknowledged_at = utcnow()
    _teams_notify_acknowledged(req, user)
    return req


def release_acknowledgement(db: Session, req: ChangeRequest, user: User) -> ChangeRequest:
    if req.status != STATUS_PENDING:
        raise RequestError(f"Request {req.id} is {req.status}, not pending")
    if req.acknowledged_by_id is None:
        return req
    req.acknowledged_by_id = None
    req.acknowledged_at = None
    _teams_notify_released(req, user)
    return req


def _teams_notify_pending(req: ChangeRequest) -> None:
    """Best-effort: a Teams outage must not block the local VLAN request."""
    try:
        teams.notify_vlan_pending(req)
    except TeamsError as exc:
        log.warning("Teams notify failed for request %s: %s", req.id, exc)


def _teams_notify_acknowledged(req: ChangeRequest, actor: User) -> None:
    try:
        teams.notify_acknowledged(req, actor)
    except TeamsError as exc:
        log.warning("Teams acknowledge notify failed for request %s: %s", req.id, exc)


def _teams_notify_released(req: ChangeRequest, actor: User) -> None:
    try:
        teams.notify_released(req, actor)
    except TeamsError as exc:
        log.warning("Teams release notify failed for request %s: %s", req.id, exc)


def _sn_after_decision(req: ChangeRequest, approved: bool) -> None:
    if req.request_type != REQUEST_VLAN:
        return
    note = req.review_note or ("Approved and executed in Switcheroo." if approved else "Rejected in Switcheroo.")
    if req.status == STATUS_FAILED:
        note += f" Switch write failed: {req.error_message}"
    try:
        if approved and req.status == STATUS_EXECUTED:
            servicenow.resolve_ticket(req, note)
        elif approved and req.status == STATUS_FAILED:
            servicenow.cancel_ticket(req, note)
        else:
            servicenow.cancel_ticket(req, note)
    except ServiceNowError as exc:
        log.warning("ServiceNow follow-up failed for request %s: %s", req.id, exc)
        req.servicenow_note = (req.servicenow_note or "") + f" SN follow-up failed: {exc}"


def sync_servicenow_tickets(db: Session) -> int:
    """Match poll results to local VLAN requests (restart recovery). Never raises out."""
    try:
        rows = servicenow.poll_open_tickets()
    except Exception as exc:  # noqa: BLE001
        log.warning("ServiceNow poll failed: %s", exc)
        return 0
    updated = 0
    for row in rows:
        corr = str(row.get("correlation_id") or "")
        if not corr.startswith("switcheroo:vlan:"):
            continue
        try:
            req_id = int(corr.rsplit(":", 1)[-1])
        except ValueError:
            continue
        req = db.get(ChangeRequest, req_id)
        if req is None:
            continue
        number = row.get("number")
        sys_id = row.get("sys_id")
        changed = False
        if number and req.servicenow_ticket != number:
            req.servicenow_ticket = number
            changed = True
        if sys_id and not req.servicenow_sys_id:
            req.servicenow_sys_id = sys_id
            changed = True
        if not req.servicenow_correlation_id:
            req.servicenow_correlation_id = corr
            changed = True
        if changed:
            updated += 1
    return updated
