from __future__ import annotations

import logging

import pytest

from app.diagnostics import (
    desired_log_level,
    diagnostics_enabled,
    diagnostics_log_path,
    flag_path,
    set_diagnostics_flag,
    step,
    sync_log_level,
)


def test_diagnostics_env_enables_debug(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_DIAGNOSTICS", "true")
    assert diagnostics_enabled()
    assert desired_log_level() == logging.DEBUG
    monkeypatch.setenv("SWITCHEROO_DIAGNOSTICS", "false")
    assert not diagnostics_enabled()
    assert desired_log_level() == logging.INFO


def test_diagnostics_flag_file_sets_debug():
    assert not diagnostics_enabled()
    assert desired_log_level() == logging.INFO
    set_diagnostics_flag(True)
    try:
        assert flag_path().is_file()
        assert diagnostics_enabled()
        assert desired_log_level() == logging.DEBUG
        sync_log_level()
        assert logging.getLogger("switcheroo").level == logging.DEBUG
    finally:
        set_diagnostics_flag(False)
    assert not diagnostics_enabled()
    assert desired_log_level() == logging.INFO
    assert logging.getLogger("switcheroo").level == logging.INFO


def test_step_writes_begin_ok_and_fail(caplog):
    set_diagnostics_flag(True)
    try:
        with caplog.at_level(logging.DEBUG, logger="switcheroo.step"):
            with step("cisco.restconf", switch="CS-BLD-A-AS01", **{"if": "Gi1/0/1"}):
                pass
            with pytest.raises(RuntimeError, match="ConnectTimeout"):
                with step("cisco.restconf", switch="CS-BLD-A-AS01", **{"if": "Gi1/0/1"}):
                    raise RuntimeError("ConnectTimeout")
        text = caplog.text
        assert "cisco.restconf begin" in text
        assert "cisco.restconf ok" in text
        assert "cisco.restconf fail" in text
        assert "switch=CS-BLD-A-AS01" in text
        assert "if=Gi1/0/1" in text
        assert "elapsed_ms=" in text
        assert "error=ConnectTimeout" in text
        log_text = diagnostics_log_path().read_text(encoding="utf-8")
        assert "cisco.restconf begin" in log_text
        assert "cisco.restconf ok" in log_text
        assert "cisco.restconf fail" in log_text
    finally:
        set_diagnostics_flag(False)


def test_step_redacts_password_and_webhook(caplog):
    set_diagnostics_flag(True)
    try:
        with caplog.at_level(logging.DEBUG, logger="switcheroo.step"):
            with step(
                "teams.notify",
                request_id=7,
                password="hunter2-not-for-logs",
                webhook="https://example.webhook.office.com/secret-token",
            ):
                pass
        text = caplog.text
        assert "teams.notify begin" in text
        assert "teams.notify ok" in text
        assert "hunter2-not-for-logs" not in text
        assert "secret-token" not in text
        assert "webhook=" not in text
        assert "password=" not in text
        log_text = diagnostics_log_path().read_text(encoding="utf-8")
        assert "hunter2-not-for-logs" not in log_text
        assert "secret-token" not in log_text
    finally:
        set_diagnostics_flag(False)


def test_step_fail_logs_when_diagnostics_off(caplog):
    set_diagnostics_flag(False)
    with caplog.at_level(logging.DEBUG, logger="switcheroo.step"):
        with pytest.raises(ValueError, match="box down"):
            with step("poll.status", switch="CS-BLD-A-AS01"):
                raise ValueError("box down")
    assert "poll.status fail" in caplog.text
    assert "poll.status begin" not in caplog.text
    assert "error=box_down" in caplog.text
