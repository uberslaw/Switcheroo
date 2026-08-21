from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import BOOTSTRAP_PASSWORD_LENGTH, hash_password, password_meets_policy
from app.config import get_settings
from app.drivers.simulator import SimPort, simulator
from app.services.patching import is_third_aux
from app.models import (
    ROLE_CS,
    ROLE_NETWORKS,
    SOURCE_SEED,
    CablePath,
    FieldOutlet,
    Patch,
    PatchPanel,
    PatchPanelPort,
    Port,
    Switch,
    SwitchVlan,
    User,
    UserSwitchPermission,
    utcnow,
)

from app.prereq import PrerequisiteError

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

BNE_OFFICE = "Brisbane"
BNE_L27_MCR = "Level 27 Main Comms Room"
BNE_L26_IDF = "Level 26 IDF"
BNE_L21_IDF = "Level 21 IDF"


def _bne_member(
    name: str,
    management_ip: str,
    room: str,
    stack_name: str,
    stack_role: str,
    member_number: int,
    rack_order: int,
    chassis_model: str = "9300",
    notes: str = "",
) -> dict:
    model_label = "Catalyst 9500" if chassis_model == "9500" else "Catalyst 9300 48-port"
    return {
        "name": name,
        "management_ip": management_ip,
        "location": BNE_OFFICE,
        "room": room,
        "stack_name": stack_name,
        "stack_role": stack_role,
        "member_number": member_number,
        "rack_order": rack_order,
        "chassis_model": chassis_model,
        "notes": notes
        or f"Simulated {model_label}, {stack_name} member #{member_number}. TEST-NET-1 address, not a real device.",
    }


def _bne_floor(prefix: str, count: int, room: str, stack_name: str, ip_start: int) -> list[dict]:
    return [
        _bne_member(
            name=f"{prefix}-{idx:02d}",
            management_ip=f"192.0.2.{ip_start + idx - 1}",
            room=room,
            stack_name=stack_name,
            stack_role="floor",
            member_number=idx,
            rack_order=idx,
        )
        for idx in range(1, count + 1)
    ]


# Documentation TEST-NET-1 addresses — not live campus switches.
SWITCH_SPECS: tuple[dict, ...] = (
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
    *_bne_floor("BNE-L27-FS", 7, BNE_L27_MCR, "Level 27 Floor Stack", 21),
    *_bne_floor("BNE-L26-FS", 5, BNE_L26_IDF, "Level 26 Floor Stack", 31),
    *_bne_floor("BNE-L21-FS", 3, BNE_L21_IDF, "Level 21 Floor Stack", 41),
    # Aux physical order top → bottom is #3, #1, #2 (rack_order, not numeric name order).
    _bne_member("BNE-L27-AUX-03", "192.0.2.53", BNE_L27_MCR, "Level 27 Aux Stack", "aux", 3, 1),
    _bne_member("BNE-L27-AUX-01", "192.0.2.51", BNE_L27_MCR, "Level 27 Aux Stack", "aux", 1, 2),
    _bne_member("BNE-L27-AUX-02", "192.0.2.52", BNE_L27_MCR, "Level 27 Aux Stack", "aux", 2, 3),
    _bne_member(
        "BNE-L27-CORE-01",
        "192.0.2.61",
        BNE_L27_MCR,
        "Level 27 Core Stack",
        "core",
        1,
        1,
        chassis_model="9500",
    ),
    _bne_member(
        "BNE-L27-CORE-02",
        "192.0.2.62",
        BNE_L27_MCR,
        "Level 27 Core Stack",
        "core",
        2,
        2,
        chassis_model="9500",
    ),
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
    """Idempotent lab seed. Hardened mode never creates the published lab passwords."""
    if get_settings().require_hardened:
        created = _bootstrap_admin(db)
        _warn_lab_usernames(db)
        return {"users": created, "switches": 0}

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
                room=spec.get("room") or "",
                stack_name=spec.get("stack_name") or "",
                stack_role=spec.get("stack_role") or "",
                member_number=int(spec.get("member_number") or 0),
                rack_order=int(spec.get("rack_order") or 0),
                chassis_model=spec.get("chassis_model") or "9300",
                notes=spec["notes"],
                username=None,
                password=None,
                driver_override="simulator",
            )
            db.add(switch)
            db.flush()
            created_switches += 1
            log.info("Seeded simulated switch %s", spec["name"])
        else:
            # Upsert layout so an old DB (lab-only, or columns added empty) still
            # groups into Brisbane racks. Leave credentials / driver as-is.
            switch.location = spec["location"]
            switch.room = spec.get("room") or ""
            switch.stack_name = spec.get("stack_name") or ""
            switch.stack_role = spec.get("stack_role") or ""
            switch.member_number = int(spec.get("member_number") or 0)
            switch.rack_order = int(spec.get("rack_order") or 0)
            switch.chassis_model = spec.get("chassis_model") or "9300"
            if not (switch.management_ip or "").strip():
                switch.management_ip = spec["management_ip"]
            if not (switch.notes or "").strip():
                switch.notes = spec["notes"]
            if not (switch.driver_override or "").strip():
                switch.driver_override = "simulator"

        existing_vlans = {v.vlan_id for v in switch.vlans}
        for vlan_id, vlan_name in VLANS:
            if vlan_id not in existing_vlans:
                db.add(SwitchVlan(switch_id=switch.id, vlan_id=vlan_id, vlan_name=vlan_name))

        if not switch.ports:
            for row in _port_plan(spec["name"]):
                db.add(
                    Port(
                        switch_id=switch.id,
                        data_source=SOURCE_SEED,
                        **row,
                    )
                )
            db.flush()

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

    _seed_brisbane_patching(db)

    db.commit()
    return {"users": created_users, "switches": created_switches}


def ensure_hardened_users(db: Session) -> None:
    """Fail startup if hardened mode would leave the site with nobody who can sign in."""
    settings = get_settings()
    if not settings.require_hardened:
        return
    n = db.scalar(select(func.count(User.id))) or 0
    if n == 0:
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true and there are no users. "
            "Set SWITCHEROO_BOOTSTRAP_USERNAME and SWITCHEROO_BOOTSTRAP_PASSWORD "
            f"({BOOTSTRAP_PASSWORD_LENGTH}+ characters) for the first Networks admin, "
            "then change it under Access. Lab users networks/cs are not created in "
            "hardened mode. See docs/security.md."
        )


def _bootstrap_admin(db: Session) -> int:
    settings = get_settings()
    password = settings.bootstrap_password
    username = settings.bootstrap_username
    if not password:
        return 0
    if not password_meets_policy(password, minimum=BOOTSTRAP_PASSWORD_LENGTH):
        raise PrerequisiteError(
            f"SWITCHEROO_BOOTSTRAP_PASSWORD must be at least {BOOTSTRAP_PASSWORD_LENGTH} "
            "characters. See docs/security.md."
        )
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return 0
    db.add(
        User(
            username=username,
            password_hash=hash_password(password),
            role=ROLE_NETWORKS,
            display_name="Bootstrap admin",
        )
    )
    db.commit()
    log.warning(
        "Created bootstrap Networks user %s. Change this password under Access. "
        "The password is not logged.",
        username,
    )
    return 1


def _warn_lab_usernames(db: Session) -> None:
    for name in ("networks", "cs"):
        if db.scalar(select(User).where(User.username == name)) is not None:
            log.warning(
                "Hardened mode: user %s already exists. Confirm this is not the "
                "published lab password from the README.",
                name,
            )


def _backfill_link_uptime(switch: Switch) -> None:
    """Fill missing lab uptime on already-up ports after a schema add or old seed."""
    now = utcnow()
    for port in switch.ports:
        up = port.oper_status == "up" and port.admin_status != "down"
        if up and port.link_up_since is None:
            port.link_up_since = now - timedelta(hours=1 + (port.if_index % 17), minutes=(port.if_index * 7) % 60)
        elif not up:
            port.link_up_since = None


def _seed_brisbane_patching(db: Session) -> None:
    """24-port TERA-MAX style panels in the RU above and below each floor 9300.

    A 20 cm patch cord reaches the closest switch ports: above → Gi1/0/1–24,
    below → Gi1/0/25–48. Aux #1 and #2 have the same FO sandwich; aux #3 (top)
    has horizontal cable routing above and below, no field panels. Core has none.
    """
    _remove_legacy_placeholder_panels(db)
    floors = (
        ("Level 27 Floor Stack", "L27", "FO-27", BNE_L27_MCR),
        ("Level 26 Floor Stack", "L26", "FO-26", BNE_L26_IDF),
        ("Level 21 Floor Stack", "L21", "FO-21", BNE_L21_IDF),
    )
    for stack_name, floor, fo_prefix, room in floors:
        members = list(
            db.scalars(
                select(Switch)
                .where(Switch.location == BNE_OFFICE, Switch.stack_name == stack_name)
                .order_by(Switch.rack_order, Switch.member_number)
            ).all()
        )
        seq = 1
        for switch in members:
            seq = _seed_member_panels(db, switch, floor, room, fo_prefix, seq)
            if switch.name == "BNE-L27-FS-01":
                ports = {p.if_index: p for p in switch.ports}
                spare = ports.get(15)
                if spare is not None:
                    spare.faulty = True
                shut = ports.get(39)
                if shut is not None:
                    shut.admin_status = "down"
                    shut.oper_status = "down"

    _seed_aux_patching(db)


def _seed_aux_patching(db: Session) -> None:
    """Aux rack: #3 on top with cable managers; #1 and #2 with FO above and below."""
    aux = list(
        db.scalars(
            select(Switch)
            .where(Switch.location == BNE_OFFICE, Switch.stack_role == "aux")
            .order_by(Switch.member_number)
        ).all()
    )
    by_member = {s.member_number: s for s in aux}
    for member, prefix in ((1, "FO-A1"), (2, "FO-A2")):
        switch = by_member.get(member)
        if switch is not None and not is_third_aux(switch):
            _seed_member_panels(db, switch, "AUX", BNE_L27_MCR, prefix, 1)
    for switch in aux:
        if not is_third_aux(switch):
            continue
        for placement in ("above", "below"):
            leftover = db.scalar(
                select(PatchPanel).where(PatchPanel.name == f"{switch.name}-PP-{placement.upper()}")
            )
            if leftover is not None:
                db.delete(leftover)
                db.flush()
        _empty_third_aux_ports(switch)


def _empty_third_aux_ports(switch: Switch) -> None:
    """3rd aux is the empty switch — no desk FOs, copper ports sit unused."""
    for port in switch.ports:
        if (port.purpose or "") == "uplink":
            continue
        port.purpose = "unused"
        port.friendly_label = ""
        port.oper_status = "down"
        port.admin_status = "up"
        port.vlan_id = None
        port.vlan_name = None
        port.mac_address = None
        port.ip_address = None
        port.ise_status = "none"
        port.link_up_since = None
        port.faulty = False
    for port in switch.ports:
        simulator.hydrate_from_port(switch, port)


def _remove_legacy_placeholder_panels(db: Session) -> None:
    legacy = ("BNE-L27-PP-01", "BNE-L26-PP-01", "BNE-L21-PP-01")
    for name in legacy:
        panel = db.scalar(select(PatchPanel).where(PatchPanel.name == name))
        if panel is not None:
            db.delete(panel)
            db.flush()


def _seed_member_panels(
    db: Session,
    switch: Switch,
    floor: str,
    room: str,
    fo_prefix: str,
    seq: int,
) -> int:
    ports = {p.if_index: p for p in switch.ports}
    for placement, port_base, patch_first in (("above", 0, 12), ("below", 24, 0)):
        panel_name = f"{switch.name}-PP-{placement.upper()}"
        panel = db.scalar(select(PatchPanel).where(PatchPanel.name == panel_name))
        if panel is None:
            panel = PatchPanel(
                name=panel_name,
                room=room,
                location=BNE_OFFICE,
                port_count=24,
                switch_id=switch.id,
                placement=placement,
            )
            db.add(panel)
            db.flush()
            for pos in range(1, 25):
                db.add(PatchPanelPort(panel_id=panel.id, position=pos, field_number=str(pos)))
            db.flush()
        else:
            panel.switch_id = switch.id
            panel.placement = placement
            panel.room = room
            panel.location = BNE_OFFICE
        jacks = {
            p.position: p
            for p in db.scalars(select(PatchPanelPort).where(PatchPanelPort.panel_id == panel.id)).all()
        }
        for pos in range(1, 25):
            code = f"{fo_prefix}{seq:03d}"
            seq += 1
            outlet = db.scalar(select(FieldOutlet).where(FieldOutlet.code == code))
            if outlet is None:
                outlet = FieldOutlet(
                    code=code,
                    label=f"{floor} {placement} jack {pos:02d} · {switch.name}",
                    floor=floor,
                    room=room,
                    faulty=False,
                )
                db.add(outlet)
                db.flush()
            jack = jacks.get(pos)
            if jack is None:
                continue
            jack.field_number = str(pos)
            jack.field_outlet_id = outlet.id
            switch_port = ports.get(port_base + pos)
            _ensure_cable(
                db,
                "field_outlet",
                outlet.id,
                "panel_port",
                jack.id,
                None,
                "stub: field horizontal",
            )
            if patch_first and pos <= patch_first and switch_port is not None:
                existing = db.scalar(select(Patch).where(Patch.field_outlet_id == outlet.id))
                taken = db.scalar(select(Patch).where(Patch.port_id == switch_port.id))
                if existing is None and taken is None:
                    db.add(
                        Patch(
                            field_outlet_id=outlet.id,
                            port_id=switch_port.id,
                            panel_port_id=jack.id,
                        )
                    )
                elif existing is not None:
                    existing.panel_port_id = jack.id
                _ensure_cable(
                    db,
                    "panel_port",
                    jack.id,
                    "switch_port",
                    switch_port.id,
                    0.20,
                    "20 cm patch to closest RU",
                )
    return seq


def _ensure_cable(
    db: Session,
    from_kind: str,
    from_id: int,
    to_kind: str,
    to_id: int,
    length_m: float | None,
    path_note: str,
) -> None:
    row = db.scalar(
        select(CablePath).where(
            CablePath.from_kind == from_kind,
            CablePath.from_id == from_id,
            CablePath.to_kind == to_kind,
            CablePath.to_id == to_id,
        )
    )
    if row is None:
        db.add(
            CablePath(
                from_kind=from_kind,
                from_id=from_id,
                to_kind=to_kind,
                to_id=to_id,
                length_m=length_m,
                path_note=path_note,
            )
        )
        return
    if length_m is not None:
        row.length_m = length_m
        row.path_note = path_note
