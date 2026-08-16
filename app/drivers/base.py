from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models import Switch
from app.schemas import InterfaceDetails, InterfaceStatus


class DriverError(Exception):
    """A switch operation failed. The poller must record this, not crash."""


class DriverUnavailable(DriverError):
    """Credentials or reachability missing — stay off the wire."""


@runtime_checkable
class SwitchDriver(Protocol):
    name: str

    def poll_interface_status(self, switch: Switch, if_names: list[str]) -> list[InterfaceStatus]:
        """Targeted ifOperStatus/admin status only. Never a full table walk."""

    def poll_interface_details(self, switch: Switch, if_name: str) -> InterfaceDetails:
        """VLAN, MAC, IP, ISE/auth session, plus current link state."""

    def set_access_vlan(self, switch: Switch, if_name: str, vlan_id: int, vlan_name: str = "") -> None:
        ...

    def bounce_port(self, switch: Switch, if_name: str) -> None:
        ...

    def no_shutdown(self, switch: Switch, if_name: str) -> None:
        ...

    def shutdown(self, switch: Switch, if_name: str) -> None:
        ...
