from __future__ import annotations

import logging

from app.config import get_settings
from app.drivers.base import SwitchDriver
from app.drivers.cisco_iosxe import CiscoIOSXEDriver
from app.drivers.simulator import simulator
from app.models import Switch

log = logging.getLogger("switcheroo.driver")

_cisco = CiscoIOSXEDriver()


def get_driver(switch: Switch) -> SwitchDriver:
    """Pick a driver per switch. Missing Cisco credentials stay on the simulator."""
    settings = get_settings()
    requested = (switch.driver_override or settings.driver or "simulator").strip().lower()
    if requested == "cisco_iosxe":
        if switch.username and switch.password and switch.management_ip:
            return _cisco
        log.warning(
            "Switch %s requested cisco_iosxe but has no IP/credentials; using simulator",
            switch.name,
        )
        return simulator
    return simulator
