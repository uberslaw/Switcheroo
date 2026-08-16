from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.drivers.base import DriverError
from app.drivers.factory import get_driver
from app.models import (
    ROLE_NETWORKS,
    SOURCE_DAILY,
    SOURCE_LIVE,
    SOURCE_TROUBLESHOOT,
    SOURCE_WRITE,
    Port,
    Switch,
    TroubleshootingSession,
    User,
    UserSwitchPermission,
    utcnow,
)
from app.schemas import InterfaceDetails
from app.services.cooldown import CooldownActive, assert_can_refresh, can_refresh, mark_refreshed
from app.services.uptime import apply_link_uptime

log = logging.getLogger("switcheroo.switch")


class PermissionDenied(Exception):
    pass


class TroubleshootConflict(Exception):
    pass


def visible_switches(db: Session, user: User) -> list[Switch]:
    if user.role == ROLE_NETWORKS:
        return list(db.scalars(select(Switch).order_by(Switch.name)).all())
    rows = db.scalars(
        select(Switch)
        .join(UserSwitchPermission, UserSwitchPermission.switch_id == Switch.id)
        .where(UserSwitchPermission.user_id == user.id)
        .order_by(Switch.name)
    ).all()
    return list(rows)


def get_switch_for_user(db: Session, user: User, switch_id: int) -> Switch:
    switch = db.get(Switch, switch_id)
    if switch is None:
        raise PermissionDenied("Switch not found")
    if user.role == ROLE_NETWORKS:
        return switch
    allowed = db.scalar(
        select(UserSwitchPermission).where(
            UserSwitchPermission.user_id == user.id,
            UserSwitchPermission.switch_id == switch_id,
        )
    )
    if allowed is None:
        raise PermissionDenied("You do not have access to this switch")
    return switch


def user_can_access_switch(db: Session, user: User, switch_id: int) -> bool:
    try:
        get_switch_for_user(db, user, switch_id)
        return True
    except PermissionDenied:
        return False


def apply_status(port: Port, oper_status: str, admin_status: str) -> None:
    apply_link_uptime(port, oper_status, admin_status)
    port.oper_status = oper_status
    port.admin_status = admin_status
    port.last_status_poll_at = utcnow()
    port.last_poll_error = None


def apply_details(port: Port, details: InterfaceDetails, source: str) -> None:
    apply_link_uptime(port, details.oper_status, details.admin_status)
    port.oper_status = details.oper_status
    port.admin_status = details.admin_status
    port.vlan_id = details.vlan_id
    port.vlan_name = details.vlan_name
    port.mac_address = details.mac_address
    port.ip_address = details.ip_address
    port.ise_status = details.ise_status
    port.last_status_poll_at = utcnow()
    port.last_detail_poll_at = utcnow()
    port.data_source = source
    port.last_poll_error = None


def poll_switch_status(db: Session, switch: Switch) -> None:
    """Targeted ifOperStatus for configured ports only."""
    ports = list(db.scalars(select(Port).where(Port.switch_id == switch.id).order_by(Port.if_index)).all())
    if not ports:
        switch.last_status_poll_at = utcnow()
        switch.next_status_poll_at = utcnow() + timedelta(seconds=get_settings().status_poll_interval)
        switch.last_poll_error = None
        return
    driver = get_driver(switch)
    try:
        statuses = driver.poll_interface_status(switch, [p.if_name for p in ports])
        by_name = {s.if_name: s for s in statuses}
        for port in ports:
            status = by_name.get(port.if_name)
            if status is None:
                continue
            apply_status(port, status.oper_status, status.admin_status)
        switch.last_status_poll_at = utcnow()
        switch.last_poll_error = None
    except Exception as exc:  # noqa: BLE001 — poller must not kill the web app
        log.exception("Status poll failed for %s", switch.name)
        switch.last_poll_error = str(exc)
        for port in ports:
            port.last_poll_error = str(exc)
    switch.next_status_poll_at = utcnow() + timedelta(seconds=get_settings().status_poll_interval)


def poll_switch_daily(db: Session, switch: Switch) -> None:
    ports = list(db.scalars(select(Port).where(Port.switch_id == switch.id).order_by(Port.if_index)).all())
    driver = get_driver(switch)
    errors: list[str] = []
    for port in ports:
        try:
            details = driver.poll_interface_details(switch, port.if_name)
            apply_details(port, details, SOURCE_DAILY)
        except Exception as exc:  # noqa: BLE001
            log.warning("Daily detail poll failed for %s %s: %s", switch.name, port.if_name, exc)
            port.last_poll_error = str(exc)
            errors.append(f"{port.if_name}: {exc}")
    switch.last_daily_poll_at = utcnow()
    switch.last_poll_error = "; ".join(errors) if errors else None


def refresh_port(db: Session, port: Port, source: str = SOURCE_LIVE, honor_cooldown: bool = True) -> Port:
    if honor_cooldown:
        assert_can_refresh(port)
    driver = get_driver(port.switch)
    try:
        details = driver.poll_interface_details(port.switch, port.if_name)
        apply_details(port, details, source)
        if honor_cooldown:
            mark_refreshed(port)
    except CooldownActive:
        raise
    except Exception as exc:  # noqa: BLE001
        port.last_poll_error = str(exc)
        if honor_cooldown:
            mark_refreshed(port)
        raise DriverError(str(exc)) from exc
    return port


def active_session_for_user(db: Session, user_id: int) -> Optional[TroubleshootingSession]:
    return db.scalar(
        select(TroubleshootingSession).where(
            TroubleshootingSession.user_id == user_id,
            TroubleshootingSession.is_active.is_(True),
        )
    )


def start_troubleshooting(db: Session, user: User, port: Port) -> TroubleshootingSession:
    existing = active_session_for_user(db, user.id)
    if existing is not None:
        raise TroubleshootConflict(
            f"You already have an active session on {existing.port.if_name} "
            f"(ends {existing.ends_at.isoformat(sep=' ', timespec='seconds')} UTC). Stop it first."
        )
    now = utcnow()
    session = TroubleshootingSession(
        user_id=user.id,
        switch_id=port.switch_id,
        port_id=port.id,
        started_at=now,
        ends_at=now + timedelta(seconds=get_settings().troubleshoot_duration),
        is_active=True,
    )
    db.add(session)
    db.flush()
    try:
        refresh_port(db, port, source=SOURCE_TROUBLESHOOT, honor_cooldown=False)
        session.last_tick_at = utcnow()
    except Exception as exc:  # noqa: BLE001
        session.last_error = str(exc)
        port.last_poll_error = str(exc)
    return session


def stop_troubleshooting(session: TroubleshootingSession) -> None:
    session.is_active = False
    session.stopped_at = utcnow()


def tick_troubleshooting(db: Session) -> None:
    now = utcnow()
    sessions = list(
        db.scalars(select(TroubleshootingSession).where(TroubleshootingSession.is_active.is_(True))).all()
    )
    interval = get_settings().troubleshoot_interval
    for session in sessions:
        if now >= session.ends_at:
            stop_troubleshooting(session)
            continue
        if session.last_tick_at and (now - session.last_tick_at).total_seconds() < interval:
            continue
        port = db.get(Port, session.port_id)
        if port is None:
            stop_troubleshooting(session)
            continue
        try:
            refresh_port(db, port, source=SOURCE_TROUBLESHOOT, honor_cooldown=False)
            session.last_tick_at = now
            session.last_error = None
        except Exception as exc:  # noqa: BLE001
            session.last_error = str(exc)
            session.last_tick_at = now
            log.warning("Troubleshoot tick failed session=%s: %s", session.id, exc)


def apply_write_to_port(port: Port, details_or_status: InterfaceDetails | None = None) -> None:
    if details_or_status is not None:
        apply_details(port, details_or_status, SOURCE_WRITE)
    else:
        port.data_source = SOURCE_WRITE
        port.last_status_poll_at = utcnow()


def port_cooldown_state(port: Port) -> tuple[bool, int]:
    return can_refresh(port)
