"""Helpers for Switcheroo Launch Control (status, PID, health URL, log tail).

PowerShell Launch Control mirrors these algorithms in scripts/Switcheroo.Monitor.ps1.
Keep both in sync; tests cover this module and invoke the PowerShell parsers.
"""

from __future__ import annotations

import re
from pathlib import Path

SERVICE_NEVER_STARTED = 1077
DEFAULT_TAIL_LINES = 200
_TAIL_READ_CAP = 256_000
_TAIL_FOLLOW_CAP = 65_536

_SC_PID = re.compile(r"(?im)^\s*PID\s*:\s*(\d+)")


def probe_host(bind_host: str) -> str:
    """Host to use for local HTTP probes. All-interfaces binds are not reachable as-is."""
    if bind_host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return bind_host


def probe_url(bind_host: str, port: int) -> str:
    return f"http://{probe_host(bind_host)}:{int(port)}"


def health_url(bind_host: str, port: int) -> str:
    return probe_url(bind_host, port).rstrip("/") + "/health"


def parse_sc_queryex_pid(text: str) -> int:
    """Parse PID from `sc.exe queryex` output. 0 means stopped / unknown."""
    match = _SC_PID.search(text or "")
    if not match:
        return 0
    return int(match.group(1))


def parse_netstat_listen_pid(text: str, port: int) -> int:
    """Parse OwningProcess for a LISTENING socket from `netstat -ano` text."""
    pattern = re.compile(rf"(?im):{int(port)}\s+\S+\s+LISTENING\s+(\d+)")
    match = pattern.search(text or "")
    if not match:
        return 0
    return int(match.group(1))


def _norm_state(service_state: str) -> str:
    return "".join((service_state or "").lower().split())


def map_monitor_status(
    *,
    service_exists: bool,
    service_state: str = "",
    service_exit_code: int = 0,
    health_ok: bool = False,
    pid_alive: bool = False,
    starting_grace: bool = False,
) -> str:
    """Map service + health + process into Launch Control status text."""
    if service_exists:
        key = _norm_state(service_state)
        if key == "startpending":
            return "Starting"
        if key == "stoppending":
            return "Stopping"
        if key == "running":
            if health_ok:
                return "Running"
            if starting_grace:
                return "Starting"
            return "Unreachable"
        if key == "stopped":
            if service_exit_code not in (0, SERVICE_NEVER_STARTED):
                return "Stopped (failed)"
            return "Stopped"
        if key == "paused":
            return "Stopped"

    if health_ok:
        return "Running"
    if pid_alive:
        return "Starting" if starting_grace else "Unreachable"
    return "Stopped"


def format_pid_label(pid: int, status: str) -> str:
    if pid > 0:
        return f"PID {pid}"
    if status in {"Running", "Starting", "Unreachable", "Stopping"}:
        return "PID resolving..."
    return "PID -"


def format_health_label(ok: bool | None, latency_ms: int | None, error: str | None = None) -> str:
    if ok is None:
        return "Health: not checked"
    latency = f"{int(latency_ms)}ms" if latency_ms is not None else "-"
    if ok:
        return f"Health: ok {latency}"
    detail = (error or "fail").strip().replace("\n", " ")
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return f"Health: fail {latency} ({detail})"


def tail_log_lines(
    path: str | Path,
    max_lines: int = DEFAULT_TAIL_LINES,
    after_offset: int = 0,
) -> tuple[list[str], int]:
    """Return (lines, new_offset). after_offset=0 means last max_lines; else follow."""
    log_path = Path(path)
    if not log_path.is_file():
        return [], 0
    size = log_path.stat().st_size
    if after_offset > 0 and after_offset <= size:
        with log_path.open("rb") as handle:
            remain = size - after_offset
            if remain > _TAIL_FOLLOW_CAP:
                handle.seek(-_TAIL_FOLLOW_CAP, 2)
            else:
                handle.seek(after_offset)
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines, size

    with log_path.open("rb") as handle:
        if size > _TAIL_READ_CAP:
            handle.seek(-_TAIL_READ_CAP, 2)
        data = handle.read()
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if max_lines > 0:
        lines = lines[-max_lines:]
    return lines, size
