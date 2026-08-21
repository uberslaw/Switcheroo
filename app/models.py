from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    room: Mapped[str] = mapped_column(String(128), default="")
    stack_name: Mapped[str] = mapped_column(String(128), default="")
    stack_role: Mapped[str] = mapped_column(String(32), default="")
    member_number: Mapped[int] = mapped_column(Integer, default=0)
    rack_order: Mapped[int] = mapped_column(Integer, default=0)
    chassis_model: Mapped[str] = mapped_column(String(32), default="9300")
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
    patch_panels: Mapped[list["PatchPanel"]] = relationship(back_populates="switch")

    @property
    def is_9500(self) -> bool:
        model = (self.chassis_model or "").lower()
        return model in {"9500", "c9500"}


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
    faulty: Mapped[bool] = mapped_column(default=False)

    switch: Mapped[Switch] = relationship(back_populates="ports")
    patch: Mapped[Optional["Patch"]] = relationship(back_populates="port", uselist=False)


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
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    windows_account: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    servicenow_ticket: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    servicenow_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    servicenow_sys_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    servicenow_correlation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    sn_req_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sn_ritm_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sn_req_sys_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sn_ritm_sys_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    auto_approved: Mapped[bool] = mapped_column(default=False)
    auto_approve_reason: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    requester: Mapped[User] = relationship(foreign_keys=[requester_id], back_populates="requests")
    reviewer: Mapped[Optional[User]] = relationship(foreign_keys=[reviewer_id])
    switch: Mapped[Switch] = relationship()
    port: Mapped[Port] = relationship()

    @property
    def sn_primary(self) -> Optional[str]:
        """RITM first — that is the number CS/Networks quote — then REQ, then legacy ticket."""
        return self.sn_ritm_number or self.sn_req_number or self.servicenow_ticket

    @property
    def sn_secondary(self) -> Optional[str]:
        if self.sn_ritm_number and self.sn_req_number:
            return self.sn_req_number
        return None


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


class FieldOutlet(Base):
    __tablename__ = "field_outlets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    floor: Mapped[str] = mapped_column(String(32), default="")
    room: Mapped[str] = mapped_column(String(128), default="")
    faulty: Mapped[bool] = mapped_column(default=False)

    patch: Mapped[Optional["Patch"]] = relationship(back_populates="field_outlet", uselist=False)


class PatchPanel(Base):
    __tablename__ = "patch_panels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    room: Mapped[str] = mapped_column(String(128), default="")
    location: Mapped[str] = mapped_column(String(256), default="")
    port_count: Mapped[int] = mapped_column(Integer, default=24)
    switch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("switches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    placement: Mapped[str] = mapped_column(String(16), default="")

    switch: Mapped[Optional[Switch]] = relationship(back_populates="patch_panels")
    ports: Mapped[list["PatchPanelPort"]] = relationship(
        back_populates="panel", cascade="all, delete-orphan"
    )


class PatchPanelPort(Base):
    __tablename__ = "patch_panel_ports"
    __table_args__ = (UniqueConstraint("panel_id", "position", name="uq_panel_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("patch_panels.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    field_number: Mapped[str] = mapped_column(String(32), default="")
    field_outlet_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("field_outlets.id", ondelete="SET NULL"), nullable=True, index=True
    )

    panel: Mapped[PatchPanel] = relationship(back_populates="ports")
    field_outlet: Mapped[Optional[FieldOutlet]] = relationship()
    patch: Mapped[Optional["Patch"]] = relationship(back_populates="panel_port", uselist=False)


class Patch(Base):
    """One field outlet to one switch port. Panel port is the jack on the patch panel graphic."""

    __tablename__ = "patches"
    __table_args__ = (
        UniqueConstraint("field_outlet_id", name="uq_patch_fo"),
        UniqueConstraint("port_id", name="uq_patch_switch_port"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_outlet_id: Mapped[int] = mapped_column(ForeignKey("field_outlets.id", ondelete="CASCADE"), index=True)
    port_id: Mapped[int] = mapped_column(ForeignKey("ports.id", ondelete="CASCADE"), index=True)
    panel_port_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("patch_panel_ports.id", ondelete="SET NULL"), nullable=True, index=True
    )

    field_outlet: Mapped[FieldOutlet] = relationship(back_populates="patch")
    port: Mapped[Port] = relationship(back_populates="patch")
    panel_port: Mapped[Optional[PatchPanelPort]] = relationship(back_populates="patch")


class CablePath(Base):
    """Horizontal runs stay unmeasured; patch cords to the adjacent RU are 0.20 m."""

    __tablename__ = "cable_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_kind: Mapped[str] = mapped_column(String(32), default="")
    from_id: Mapped[int] = mapped_column(Integer, default=0)
    to_kind: Mapped[str] = mapped_column(String(32), default="")
    to_id: Mapped[int] = mapped_column(Integer, default=0)
    length_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    path_note: Mapped[str] = mapped_column(String(256), default="")
