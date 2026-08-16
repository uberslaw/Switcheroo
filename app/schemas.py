from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class InterfaceStatus:
    if_name: str
    oper_status: str
    admin_status: str


@dataclass
class InterfaceDetails:
    if_name: str
    oper_status: str
    admin_status: str
    vlan_id: Optional[int] = None
    vlan_name: Optional[str] = None
    mac_address: Optional[str] = None
    ip_address: Optional[str] = None
    ise_status: Optional[str] = None


@dataclass
class PortView:
    port_id: int
    if_name: str
    if_index: int
    purpose: str
    friendly_label: str
    oper_status: str
    admin_status: str
    vlan_id: Optional[int]
    vlan_name: Optional[str]
    mac_address: Optional[str]
    ip_address: Optional[str]
    ise_status: Optional[str]
    last_status_poll_at: Optional[datetime]
    last_detail_poll_at: Optional[datetime]
    last_on_demand_at: Optional[datetime]
    last_poll_error: Optional[str]
    data_source: str
    cooldown_remaining: int
    can_refresh: bool
