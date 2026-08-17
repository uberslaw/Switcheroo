from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.launch_monitor import (
    format_health_label,
    format_pid_label,
    health_url,
    map_monitor_status,
    parse_netstat_listen_pid,
    parse_sc_queryex_pid,
    probe_host,
    probe_url,
    tail_log_lines,
)

REPO = Path(__file__).resolve().parent.parent
MONITOR_PS1 = REPO / "scripts" / "Switcheroo.Monitor.ps1"

SC_QUERYEX_RUNNING = """
SERVICE_NAME: Switcheroo
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 4  RUNNING
                                (STOPPABLE, NOT_PAUSABLE, ACCEPTS_SHUTDOWN)
        WIN32_EXIT_CODE    : 0  (0x0)
        SERVICE_EXIT_CODE  : 0  (0x0)
        CHECKPOINT         : 0x0
        WAIT_HINT          : 0x0
        PID                : 4321
        FLAGS              :
"""

SC_QUERYEX_STOPPED = """
SERVICE_NAME: Switcheroo
        TYPE               : 10  WIN32_OWN_PROCESS
        STATE              : 1  STOPPED
        WIN32_EXIT_CODE    : 0  (0x0)
        PID                : 0
        FLAGS              :
"""

NETSTAT_SAMPLE = """
  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       9876
  TCP    127.0.0.1:443          0.0.0.0:0              LISTENING       4
  TCP    [::]:8080              [::]:0                 LISTENING       9876
"""


def test_health_url_rewrites_all_interfaces():
    assert probe_host("0.0.0.0") == "127.0.0.1"
    assert probe_host("::") == "127.0.0.1"
    assert probe_host("127.0.0.1") == "127.0.0.1"
    assert health_url("0.0.0.0", 8080) == "http://127.0.0.1:8080/health"
    assert health_url("127.0.0.1", 8080) == "http://127.0.0.1:8080/health"
    assert probe_url("10.1.2.3", 9090) == "http://10.1.2.3:9090"


def test_parse_sc_queryex_pid():
    assert parse_sc_queryex_pid(SC_QUERYEX_RUNNING) == 4321
    assert parse_sc_queryex_pid(SC_QUERYEX_STOPPED) == 0
    assert parse_sc_queryex_pid("") == 0
    assert parse_sc_queryex_pid("no pid here") == 0


def test_parse_netstat_listen_pid():
    assert parse_netstat_listen_pid(NETSTAT_SAMPLE, 8080) == 9876
    assert parse_netstat_listen_pid(NETSTAT_SAMPLE, 443) == 4
    assert parse_netstat_listen_pid(NETSTAT_SAMPLE, 9999) == 0
    assert parse_netstat_listen_pid("TCP 10.0.0.8:18080 LISTENING 1", 8080) == 0


def test_map_monitor_status_service_and_process():
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Running",
            health_ok=True,
            pid_alive=True,
        )
        == "Running"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Start Pending",
            health_ok=False,
            pid_alive=True,
        )
        == "Starting"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Stop Pending",
            health_ok=False,
            pid_alive=True,
        )
        == "Stopping"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Stopped",
            service_exit_code=0,
        )
        == "Stopped"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Stopped",
            service_exit_code=1077,
        )
        == "Stopped"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Stopped",
            service_exit_code=1066,
        )
        == "Stopped (failed)"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Running",
            health_ok=False,
            pid_alive=True,
            starting_grace=True,
        )
        == "Starting"
    )
    assert (
        map_monitor_status(
            service_exists=True,
            service_state="Running",
            health_ok=False,
            pid_alive=True,
            starting_grace=False,
        )
        == "Unreachable"
    )
    assert map_monitor_status(service_exists=False, health_ok=True, pid_alive=False) == "Running"
    assert (
        map_monitor_status(
            service_exists=False,
            health_ok=False,
            pid_alive=True,
            starting_grace=True,
        )
        == "Starting"
    )
    assert (
        map_monitor_status(
            service_exists=False,
            health_ok=False,
            pid_alive=True,
            starting_grace=False,
        )
        == "Unreachable"
    )
    assert map_monitor_status(service_exists=False, health_ok=False, pid_alive=False) == "Stopped"


def test_format_pid_and_health_labels():
    assert format_pid_label(4242, "Running") == "PID 4242"
    assert format_pid_label(0, "Running") == "PID resolving..."
    assert format_pid_label(0, "Stopped") == "PID -"
    assert format_health_label(True, 12) == "Health: ok 12ms"
    assert format_health_label(False, 2000, "timed out") == "Health: fail 2000ms (timed out)"
    assert format_health_label(None, None) == "Health: not checked"


def test_tail_log_last_lines_and_follow(tmp_path: Path):
    missing = tmp_path / "missing.log"
    assert tail_log_lines(missing) == ([], 0)

    log = tmp_path / "switcheroo.log"
    log.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    lines, offset = tail_log_lines(log, max_lines=2, after_offset=0)
    assert lines == ["three", "four"]
    assert offset == log.stat().st_size

    more, offset2 = tail_log_lines(log, max_lines=200, after_offset=offset)
    assert more == []
    assert offset2 == offset

    with log.open("a", encoding="utf-8") as handle:
        handle.write("five\n")
    follow, offset3 = tail_log_lines(log, after_offset=offset)
    assert follow == ["five"]
    assert offset3 > offset

    log.write_text("rotated-a\nrotated-b\n", encoding="utf-8")
    rotated, new_offset = tail_log_lines(log, max_lines=200, after_offset=offset3)
    assert "rotated-b" in rotated
    assert new_offset == log.stat().st_size


def test_health_endpoint_still_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["app"] == "switcheroo"


def _run_monitor_ps(expression: str) -> str:
    script = f". '{MONITOR_PS1}'; {expression}"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"PowerShell failed ({completed.returncode}): {completed.stderr}\n{completed.stdout}"
        )
    return completed.stdout.strip()


@pytest.mark.skipif(sys.platform != "win32", reason="Launch Control helpers are PowerShell")
def test_powershell_parsers_match_python():
    assert MONITOR_PS1.is_file()
    pid = _run_monitor_ps(
        f"Get-ParsedScQueryexPid -Text @'\n{SC_QUERYEX_RUNNING}\n'@"
    )
    assert pid == "4321"
    listen = _run_monitor_ps(
        "Get-ParsedNetstatListenPid -Text '  TCP    127.0.0.1:8080         0.0.0.0:0              LISTENING       9876' -Port 8080"
    )
    assert listen == "9876"
    url = _run_monitor_ps("Get-SwitcherooHealthUrl -BindHost '0.0.0.0' -Port 8080")
    assert url == "http://127.0.0.1:8080/health"
    status = _run_monitor_ps(
        "Get-MappedMonitorStatus -ServiceExists:$true -ServiceState 'Stopped' "
        "-ServiceExitCode 1066 -HealthOk:$false -PidAlive:$false -StartingGrace:$false"
    )
    assert status == "Stopped (failed)"
    running = _run_monitor_ps(
        "Get-MappedMonitorStatus -ServiceExists:$false -ServiceState '' "
        "-ServiceExitCode 0 -HealthOk:$true -PidAlive:$false -StartingGrace:$false"
    )
    assert running == "Running"

    log = REPO / "data" / "_monitor_tail_test.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    try:
        count = _run_monitor_ps(
            f"(Get-FileTailLines -Path '{log}' -MaxLines 2 -AfterOffset 0).Lines.Count"
        )
        assert count == "2"
        last = _run_monitor_ps(
            f"((Get-FileTailLines -Path '{log}' -MaxLines 2 -AfterOffset 0).Lines)[-1]"
        )
        assert last == "gamma"
    finally:
        log.unlink(missing_ok=True)
