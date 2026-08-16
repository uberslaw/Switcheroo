from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ROLE_CS = "cs"
ROLE_NETWORKS = "networks"

PORT_PURPOSES = (
    "user_desk",
    "phone",
    "ap",
    "printer",
    "uplink",
    "server",
    "unused",
)

REQUEST_VLAN = "vlan_change"
REQUEST_BOUNCE = "bounce"
REQUEST_NO_SHUTDOWN = "no_shutdown"

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"

SOURCE_SEED = "seed"
SOURCE_DAILY = "daily"
SOURCE_LIVE = "live"
SOURCE_TROUBLESHOOT = "troubleshoot"
SOURCE_WRITE = "write"


def utcnow() -> datetime:
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    permissions: Mapped[list[UserSwitchPermission]] = relationship(back_populates="user")
    requests: Mapped[list[ChangeRequest]] = relationship(
        back_populates="requester", foreign_keys="ChangeRequest.requester_id"
    )


class Switch(Base):
    __tablename__ = "switches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    management_ip: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(256), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    password: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    driver_override: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_status_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_daily_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_status_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_poll_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    ports: Mapped[list[Port]] = relationship(back_populates="switch", cascade="all, delete-orphan")
    vlans: Mapped[list[SwitchVlan]] = relationship(back_populates="switch", cascade="all, delete-orphan")
    permissions: Mapped[list[UserSwitchPermission]] = relationship(back_populates="switch")


class SwitchVlan(Base):
    __tablename__ = "switch_vlans"
    __table_args__ = (UniqueConstraint("switch_id", "vlan_id", name="uq_switch_vlan"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    switch_id: Mapped[int] = mapped_column(ForeignKey("switches.id", ondelete="CASCADE"))
    vlan_id: Mapped[int] = mapped_column(Integer)
    vlan_name: Mapped[str] = mapped_column(String(64), default="")

    switch: Mapped[Switch] = relationship(back_populates="vlans")


class Port(Base):
    __tablename__ = "ports"
    __table_args__ = (UniqueConstraint("switch_id", "if_name", name="uq_switch_if"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    switch_id: Mapped[int] = mapped_column(ForeignKey("switches.id", ondelete="CASCADE"), index=True)
    if_name: Mapped[str] = mapped_column(String(64))
    if_index: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(32), default="unused")
    friendly_label: Mapped[str] = mapped_column(String(128), default="")
    oper_status: Mapped[str] = mapped_column(String(16), default="down")
    admin_status: Mapped[str] = mapped_column(String(16), default="up")
    vlan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    vlan_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    mac_address: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ise_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_status_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_detail_poll_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_on_demand_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_poll_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data_source: Mapped[str] = mapped_column(String(32), default=SOURCE_SEED)
    link_up_since: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    switch: Mapped[Switch] = relationship(back_populates="ports")


class UserSwitchPermission(Base):
    __tablename__ = "user_switch_permissions"
    __table_args__ = (UniqueConstraint("user_id", "switch_id", name="uq_user_switch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    switch_id: Mapped[int] = mapped_column(ForeignKey("switches.id", ondelete="CASCADE"), index=True)

    user: Mapped[User] = relationship(back_populates="permissions")
    switch: Mapped[Switch] = relationship(back_populates="permissions")


class ChangeRequest(Base):
    __tablename__ = "change_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reviewer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    switch_id: Mapped[int] = mapped_column(ForeignKey("switches.id"), index=True)
    port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), index=True)
    request_type: Mapped[str] = mapped_column(String(32), index=True)
    requested_vlan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    requested_vlan_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    from_vlan_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    from_vlan_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=STATUS_PENDING, index=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    servicenow_ticket: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    servicenow_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    servicenow_sys_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    servicenow_correlation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    auto_approved: Mapped[bool] = mapped_column(default=False)
    auto_approve_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id], back_populates="requests")
    reviewer: Mapped[Optional[User]] = relationship(foreign_keys=[reviewer_id])
    acknowledged_by: Mapped[Optional[User]] = relationship(foreign_keys=[acknowledged_by_id])
    switch: Mapped[Switch] = relationship()
    port: Mapped[Port] = relationship()


class AutoApprovePolicy(Base):
    """Enabled auto-approve rules. Absence or enabled=false means off. Seed stays off."""

    __tablename__ = "auto_approve_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TroubleshootingSession(Base):
    __tablename__ = "troubleshooting_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    switch_id: Mapped[int] = mapped_column(ForeignKey("switches.id"))
    port_id: Mapped[int] = mapped_column(ForeignKey("ports.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ends_at: Mapped[datetime] = mapped_column(DateTime)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, index=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship()
    switch: Mapped[Switch] = relationship()
    port: Mapped[Port] = relationship()
