from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models import Port, utcnow

PROBLEM_OPERS = {"err-disabled", "errdisable", "error", "disabled"}
LED_UP = "light-green"
LED_NO_LINK = "dark-green"
LED_PROBLEM = "red"


def is_link_up(oper_status: str, admin_status: str) -> bool:
    if (admin_status or "").lower() == "down":
        return False
    if (oper_status or "").lower() in PROBLEM_OPERS:
        return False
    return (oper_status or "").lower() == "up"


def apply_link_uptime(
    port: Port,
    new_oper: str,
    new_admin: str,
    now: Optional[datetime] = None,
) -> None:
    """Stamp or clear link_up_since from a status transition.

    down→up (or first observation already up with no stamp): set to *now*.
    up→down or shutdown: clear. Do not invent a longer history than we have seen.
    """
    current = now or utcnow()
    was_up = is_link_up(port.oper_status, port.admin_status)
    now_up = is_link_up(new_oper, new_admin)
    if now_up:
        if port.link_up_since is None or not was_up:
            port.link_up_since = current
    else:
        port.link_up_since = None


def format_connected_for(link_up_since: Optional[datetime], now: Optional[datetime] = None) -> str:
    if link_up_since is None:
        return "Not connected"
    current = now or utcnow()
    seconds = max(0, int((current - link_up_since).total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if days or hours:
        parts.append(f"{minutes}m")
    elif minutes:
        parts.append(f"{minutes}m")
    else:
        parts.append(f"{secs}s")
    return "Connected for " + " ".join(parts)


def port_led(port: Port) -> str:
    admin = (port.admin_status or "").lower()
    oper = (port.oper_status or "").lower()
    if admin == "down" or oper in PROBLEM_OPERS or port.last_poll_error:
        return LED_PROBLEM
    if oper == "up":
        return LED_UP
    return LED_NO_LINK


def problem_reason(port: Port) -> Optional[str]:
    if (port.admin_status or "").lower() == "down":
        return "Administratively shutdown"
    if (port.oper_status or "").lower() in PROBLEM_OPERS:
        return f"Port problem ({port.oper_status})"
    if port.last_poll_error:
        return port.last_poll_error
    return None


def short_if_name(if_name: str) -> str:
    return (if_name or "").replace("GigabitEthernet", "Gi")


def faceplate_groups(ports: list[Port]) -> list[list[tuple[Port | None, Port | None]]]:
    """C9300 48-port copper: four blocks of 12, odd-over-even columns, left to right."""
    by_index = {p.if_index: p for p in ports}
    groups: list[list[tuple[Port | None, Port | None]]] = []
    for start in (1, 13, 25, 37):
        cols: list[tuple[Port | None, Port | None]] = []
        for odd in range(start, start + 12, 2):
            cols.append((by_index.get(odd), by_index.get(odd + 1)))
        groups.append(cols)
    return groups
