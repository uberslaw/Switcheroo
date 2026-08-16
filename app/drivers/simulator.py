from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from app.config import get_settings
from app.models import Port, Switch
from app.schemas import InterfaceDetails, InterfaceStatus


@dataclass
class SimPort:
    if_name: str
    if_index: int
    oper_status: str = "down"
    admin_status: str = "up"
    vlan_id: Optional[int] = None
    vlan_name: Optional[str] = None
    mac_address: Optional[str] = None
    ip_address: Optional[str] = None
    ise_status: Optional[str] = None
    bounce_count: int = 0
    vlan_changes: int = 0
    no_shutdown_count: int = 0
    shutdown_count: int = 0


@dataclass
class SimSwitch:
    name: str
    ports: dict[str, SimPort] = field(default_factory=dict)


class SimulatorDriver:
    """In-process fake Catalyst 9300. Default driver; no network I/O."""

    name = "simulator"
    _inventory: dict[str, SimSwitch] = {}

    def reset(self) -> None:
        self._inventory.clear()

    def ensure_port(self, switch_name: str, spec: SimPort) -> SimPort:
        box = self._inventory.setdefault(switch_name, SimSwitch(name=switch_name))
        existing = box.ports.get(spec.if_name)
        if existing is None:
            box.ports[spec.if_name] = spec
            return spec
        return existing

    def hydrate_from_port(self, switch: Switch, port: Port) -> SimPort:
        return self.ensure_port(
            switch.name,
            SimPort(
                if_name=port.if_name,
                if_index=port.if_index,
                oper_status=port.oper_status,
                admin_status=port.admin_status,
                vlan_id=port.vlan_id,
                vlan_name=port.vlan_name,
                mac_address=port.mac_address,
                ip_address=port.ip_address,
                ise_status=port.ise_status,
            ),
        )

    def get_port(self, switch: Switch, if_name: str) -> SimPort:
        box = self._inventory.get(switch.name)
        if box and if_name in box.ports:
            return box.ports[if_name]
        port = next((p for p in switch.ports if p.if_name == if_name), None)
        if port is not None:
            return self.hydrate_from_port(switch, port)
        return self.ensure_port(switch.name, SimPort(if_name=if_name, if_index=0))

    def poll_interface_status(self, switch: Switch, if_names: list[str]) -> list[InterfaceStatus]:
        settings = get_settings()
        results: list[InterfaceStatus] = []
        for name in if_names:
            sim = self.get_port(switch, name)
            if sim.admin_status == "down":
                sim.oper_status = "down"
            elif settings.sim_flaps and random.random() < 0.03:
                sim.oper_status = "down" if sim.oper_status == "up" else "up"
            results.append(
                InterfaceStatus(
                    if_name=sim.if_name,
                    oper_status=sim.oper_status,
                    admin_status=sim.admin_status,
                )
            )
        return results

    def poll_interface_details(self, switch: Switch, if_name: str) -> InterfaceDetails:
        sim = self.get_port(switch, if_name)
        if sim.admin_status == "down":
            sim.oper_status = "down"
        return InterfaceDetails(
            if_name=sim.if_name,
            oper_status=sim.oper_status,
            admin_status=sim.admin_status,
            vlan_id=sim.vlan_id,
            vlan_name=sim.vlan_name,
            mac_address=sim.mac_address if sim.oper_status == "up" else None,
            ip_address=sim.ip_address if sim.oper_status == "up" else None,
            ise_status=sim.ise_status if sim.oper_status == "up" else ("none" if sim.admin_status == "up" else "none"),
        )

    def set_access_vlan(self, switch: Switch, if_name: str, vlan_id: int, vlan_name: str = "") -> None:
        sim = self.get_port(switch, if_name)
        sim.vlan_id = vlan_id
        sim.vlan_name = vlan_name or sim.vlan_name
        sim.vlan_changes += 1

    def bounce_port(self, switch: Switch, if_name: str) -> None:
        sim = self.get_port(switch, if_name)
        if sim.admin_status == "down":
            sim.admin_status = "up"
        sim.oper_status = "up"
        sim.bounce_count += 1

    def no_shutdown(self, switch: Switch, if_name: str) -> None:
        sim = self.get_port(switch, if_name)
        sim.admin_status = "up"
        sim.oper_status = "up"
        sim.no_shutdown_count += 1

    def shutdown(self, switch: Switch, if_name: str) -> None:
        sim = self.get_port(switch, if_name)
        sim.admin_status = "down"
        sim.oper_status = "down"
        sim.shutdown_count += 1


simulator = SimulatorDriver()
