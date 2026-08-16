from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.drivers.simulator import SimPort, simulator
from app.models import (
    ROLE_CS,
    ROLE_NETWORKS,
    SOURCE_SEED,
    Port,
    Switch,
    SwitchVlan,
    User,
    UserSwitchPermission,
    utcnow,
)

log = logging.getLogger("switcheroo.seed")

LAB_USERS = (
    ("networks", "networks", ROLE_NETWORKS, "Networks Admin"),
    ("cs", "cs", ROLE_CS, "Client Services"),
)

VLANS = (
    (10, "USER"),
    (20, "VOICE"),
    (30, "PRINTER"),
    (40, "AP"),
    (50, "GUEST"),
    (99, "MGMT"),
)

# Documentation TEST-NET-1 addresses — not live campus switches.
SWITCH_SPECS = (
    {
        "name": "CS-BLD-A-AS01",
        "management_ip": "192.0.2.10",
        "location": "Building A / IDF-1 (simulated)",
        "notes": "Simulated Catalyst 9300 48-port. TEST-NET-1 address, not a real device.",
    },
    {
        "name": "CS-BLD-B-AS01",
        "management_ip": "192.0.2.11",
        "location": "Building B / IDF-2 (simulated)",
        "notes": "Simulated Catalyst 9300 48-port. TEST-NET-1 address, not a real device.",
    },
)


def _port_plan(switch_name: str) -> list[dict]:
    """48 GigabitEthernet1/0/x ports with mixed purpose and state."""
    plan: list[dict] = []
    for idx in range(1, 49):
        if_name = f"GigabitEthernet1/0/{idx}"
        purpose = "user_desk"
        label = f"Desk {switch_name[-1]}-{idx:02d}"
        vlan_id, vlan_name = 10, "USER"
        oper, admin = "up", "up"
        mac = ip = ise = None

        if 1 <= idx <= 20:
            purpose, label = "user_desk", f"Desk {idx:02d}"
            if idx % 5 == 0:
                oper = "down"
                mac = ip = None
                ise = "none"
            else:
                mac = f"00:11:22:33:{idx:02d}:{idx:02d}"
                ip = f"10.10.10.{idx}"
                ise = "authorized" if idx % 7 else "pending"
        elif 21 <= idx <= 24:
            purpose, label = "phone", f"Handset {idx}"
            vlan_id, vlan_name = 20, "VOICE"
            mac = f"00:1a:2b:3c:00:{idx:02d}"
            ip = f"10.20.20.{idx}"
            ise = "authorized"
        elif 25 <= idx <= 28:
            purpose, label = "ap", f"AP-{switch_name[-4]}-{idx}"
            vlan_id, vlan_name = 40, "AP"
            mac = f"aa:bb:cc:dd:00:{idx:02d}"
            ip = f"10.40.40.{idx}"
            ise = "none"
        elif 29 <= idx <= 30:
            purpose, label = "printer", f"Printer {idx}"
            vlan_id, vlan_name = 30, "PRINTER"
            mac = f"00:80:77:00:00:{idx:02d}"
            ip = f"10.30.30.{idx}"
            ise = "authorized"
        elif 31 <= idx <= 32:
            purpose, label = "uplink", f"Uplink to core {idx - 30}"
            vlan_id, vlan_name = 99, "MGMT"
            oper, admin = "up", "up"
            mac = ip = None
            ise = "none"
        elif 33 <= idx <= 36:
            purpose, label = "unused", ""
            vlan_id, vlan_name = None, None
            oper, admin = "down", "up"
            ise = "none"
        elif 37 <= idx <= 38:
            purpose, label = "user_desk", f"Hot desk {idx}"
            vlan_id, vlan_name = 50, "GUEST"
            mac = f"de:ad:00:00:00:{idx:02d}"
            ip = f"10.50.50.{idx}"
            ise = "unauthorized"
        elif 39 <= idx <= 40:
            purpose, label = "user_desk", f"Shutdown spare {idx}"
            oper, admin = "down", "down"
            vlan_id, vlan_name = 10, "USER"
            ise = "none"
        elif 41 <= idx <= 44:
            purpose, label = "server", f"Closet NIC {idx}"
            vlan_id, vlan_name = 10, "USER"
            mac = f"52:54:00:00:00:{idx:02d}"
            ip = f"10.10.20.{idx}"
            ise = "authorized"
        else:
            purpose, label = "unused", ""
            vlan_id, vlan_name = None, None
            oper, admin = "down", "up"
            ise = "none"

        # Give building B a slightly different flap pattern so the two boxes are not clones.
        if switch_name.endswith("B-AS01") and idx in {2, 8, 14}:
            oper = "down"
            mac = ip = None
            ise = "none"

        link_up_since = None
        if oper == "up" and admin != "down":
            # Lab ages only — poller never invents history beyond first observation.
            link_up_since = utcnow() - timedelta(hours=1 + (idx % 17), minutes=(idx * 7) % 60)

        plan.append(
            {
                "if_name": if_name,
                "if_index": idx,
                "purpose": purpose,
                "friendly_label": label,
                "oper_status": oper,
                "admin_status": admin,
                "vlan_id": vlan_id,
                "vlan_name": vlan_name,
                "mac_address": mac,
                "ip_address": ip,
                "ise_status": ise,
                "link_up_since": link_up_since,
            }
        )
    return plan


def seed(db: Session) -> dict[str, int]:
    """Idempotent lab seed. Re-run does not duplicate users or switches."""
    created_users = 0
    created_switches = 0

    users: dict[str, User] = {}
    for username, password, role, display in LAB_USERS:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                display_name=display,
            )
            db.add(user)
            db.flush()
            created_users += 1
            log.info("Seeded lab user %s (role=%s)", username, role)
        users[username] = user

    for spec in SWITCH_SPECS:
        switch = db.scalar(select(Switch).where(Switch.name == spec["name"]))
        if switch is None:
            switch = Switch(
                name=spec["name"],
                management_ip=spec["management_ip"],
                location=spec["location"],
                notes=spec["notes"],
                username=None,
                password=None,
                driver_override="simulator",
            )
            db.add(switch)
            db.flush()
            created_switches += 1
            for vlan_id, vlan_name in VLANS:
                db.add(SwitchVlan(switch_id=switch.id, vlan_id=vlan_id, vlan_name=vlan_name))
            for row in _port_plan(spec["name"]):
                db.add(
                    Port(
                        switch_id=switch.id,
                        data_source=SOURCE_SEED,
                        **row,
                    )
                )
            db.flush()
            log.info("Seeded simulated switch %s", spec["name"])
        else:
            existing_vlans = {v.vlan_id for v in switch.vlans}
            for vlan_id, vlan_name in VLANS:
                if vlan_id not in existing_vlans:
                    db.add(SwitchVlan(switch_id=switch.id, vlan_id=vlan_id, vlan_name=vlan_name))

        cs = users["cs"]
        link = db.scalar(
            select(UserSwitchPermission).where(
                UserSwitchPermission.user_id == cs.id,
                UserSwitchPermission.switch_id == switch.id,
            )
        )
        if link is None:
            db.add(UserSwitchPermission(user_id=cs.id, switch_id=switch.id))

        db.flush()
        db.refresh(switch)
        _backfill_link_uptime(switch)
        for port in switch.ports:
            simulator.hydrate_from_port(switch, port)

    db.commit()
    return {"users": created_users, "switches": created_switches}


def _backfill_link_uptime(switch: Switch) -> None:
    """Fill missing lab uptime on already-up ports after a schema add or old seed."""
    now = utcnow()
    for port in switch.ports:
        up = port.oper_status == "up" and port.admin_status != "down"
        if up and port.link_up_since is None:
            port.link_up_since = now - timedelta(hours=1 + (port.if_index % 17), minutes=(port.if_index * 7) % 60)
        elif not up:
            port.link_up_since = None
