from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AutoApprovePolicy, ChangeRequest, Switch, User, utcnow

SCOPE_GLOBAL = "global"
SCOPE_OFFICE = "office"
SCOPE_REQUESTOR = "requestor"


@dataclass(frozen=True)
class AutoApproveMatch:
    scope: str
    label: str
    work_notes: str


def global_key() -> str:
    return SCOPE_GLOBAL


def office_key(location: str) -> str:
    return f"{SCOPE_OFFICE}:{location}"


def requestor_key(user_id: int) -> str:
    return f"{SCOPE_REQUESTOR}:{user_id}"


def is_enabled(db: Session, key: str) -> bool:
    row = db.scalar(select(AutoApprovePolicy).where(AutoApprovePolicy.key == key))
    return bool(row and row.enabled)


def set_policy(db: Session, key: str, enabled: bool) -> AutoApprovePolicy:
    row = db.scalar(select(AutoApprovePolicy).where(AutoApprovePolicy.key == key))
    if row is None:
        row = AutoApprovePolicy(key=key, enabled=enabled, updated_at=utcnow())
        db.add(row)
    else:
        row.enabled = enabled
        row.updated_at = utcnow()
    return row


def match_auto_approve(db: Session, req: ChangeRequest) -> Optional[AutoApproveMatch]:
    """Most-open wins: global, then office of the switch, then requestor."""
    if is_enabled(db, global_key()):
        return AutoApproveMatch(
            scope=SCOPE_GLOBAL,
            label="Everywhere",
            work_notes="Auto-approved by policy: global",
        )
    switch = req.switch or db.get(Switch, req.switch_id)
    location = (switch.location or "").strip() if switch is not None else ""
    if location and is_enabled(db, office_key(location)):
        return AutoApproveMatch(
            scope=SCOPE_OFFICE,
            label=f"Office: {location}",
            work_notes=f"Auto-approved by policy: office {location}",
        )
    if is_enabled(db, requestor_key(req.requester_id)):
        requester = req.requester or db.get(User, req.requester_id)
        name = requester.username if requester is not None else str(req.requester_id)
        return AutoApproveMatch(
            scope=SCOPE_REQUESTOR,
            label=f"Requestor: {name}",
            work_notes=f"Auto-approved by policy: user {name}",
        )
    return None
