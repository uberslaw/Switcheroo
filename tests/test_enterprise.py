from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.auth_windows import (
    MappingGroupResolver,
    parse_windows_identity,
    peer_may_assert_windows_identity,
    role_from_groups,
    set_group_resolver,
)
from app.config import get_settings
from app.models import ROLE_CS, ROLE_NETWORKS, User
from app.prereq import PrerequisiteError, check_prerequisites
from app.ready import build_ready_report, write_poller_heartbeat
from app.services.backup import backup_now, sqlite_backup
from app.services.identity import current_windows_account


def _windows_env(monkeypatch) -> None:
    monkeypatch.setenv("SWITCHEROO_AUTH", "windows")
    monkeypatch.setenv("SWITCHEROO_AD_NETWORKS_GROUPS", r"CORP\Switcheroo-Networks")
    monkeypatch.setenv("SWITCHEROO_AD_CS_GROUPS", r"CORP\Switcheroo-CS")
    monkeypatch.setenv("SWITCHEROO_ALLOW_LOCAL_LOGIN", "true")
    set_group_resolver(
        MappingGroupResolver(
            {
                r"corp\jsmith": [r"CORP\Switcheroo-CS"],
                r"corp\neteng": [r"CORP\Switcheroo-Networks"],
            }
        )
    )


def test_parse_windows_identity_rejects_control_chars():
    assert parse_windows_identity("CORP\\jsmith") == r"CORP\jsmith"
    assert parse_windows_identity("CORP/jsmith") == r"CORP\jsmith"
    assert parse_windows_identity("CORP\\jsmith\nadmin") is None
    assert parse_windows_identity("") is None


def test_peer_must_be_loopback_or_trusted_proxy():
    loopback = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    lan = SimpleNamespace(client=SimpleNamespace(host="10.1.2.3"))
    assert peer_may_assert_windows_identity(loopback) is True
    assert peer_may_assert_windows_identity(lan) is False


def test_networks_group_wins(monkeypatch):
    _windows_env(monkeypatch)
    role = role_from_groups([r"CORP\Switcheroo-CS", r"CORP\Switcheroo-Networks"], get_settings())
    assert role == ROLE_NETWORKS
    assert role_from_groups([r"Switcheroo-CS"], get_settings()) == ROLE_CS
    assert role_from_groups(["Other"], get_settings()) is None


def test_windows_header_jits_cs_user(client, seeded_db, monkeypatch):
    _windows_env(monkeypatch)
    response = client.get("/", headers={"X-Remote-User": r"CORP\jsmith"})
    assert response.status_code == 200
    seeded_db.expire_all()
    user = seeded_db.scalar(select(User).where(User.username == r"CORP\jsmith"))
    assert user is not None
    assert user.role == ROLE_CS
    assert user.is_windows_user
    assert user.last_login_at is not None


def test_windows_header_denied_without_group(client, seeded_db, monkeypatch):
    _windows_env(monkeypatch)
    response = client.get("/", headers={"X-Remote-User": r"CORP\nobody"}, follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]
    assert seeded_db.scalar(select(User).where(User.username == r"CORP\nobody")) is None


def test_ticket_actor_is_signed_in_user(seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    assert current_windows_account(cs) == "cs"


def test_idle_session_expires(client, seeded_db, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_SESSION_IDLE_SECONDS", "30")
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    assert client.get("/").status_code == 200
    now = time.time()
    monkeypatch.setattr("app.session_idle.time.time", lambda: now + 120)
    bounced = client.get("/", follow_redirects=False)
    assert bounced.status_code == 303
    assert "/login" in bounced.headers["location"]


def test_hardened_refuses_open_access(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "d" * 32)
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_ALLOWED_HOSTS", "switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_OPEN_ACCESS", "true")
    with pytest.raises(PrerequisiteError, match="OPEN_ACCESS"):
        check_prerequisites(get_settings())


def test_hardened_windows_refuses_open_bind_without_trusted_proxy(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "d" * 32)
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_ALLOWED_HOSTS", "switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_OPEN_ACCESS", "false")
    monkeypatch.setenv("SWITCHEROO_AUTH", "windows")
    monkeypatch.setenv("SWITCHEROO_AD_NETWORKS_GROUPS", r"CORP\Nets")
    monkeypatch.setenv("SWITCHEROO_AD_CS_GROUPS", r"CORP\CS")
    monkeypatch.setenv("SWITCHEROO_HOST", "0.0.0.0")
    monkeypatch.setenv("SWITCHEROO_TRUST_X_FORWARDED_FOR", "false")
    with pytest.raises(PrerequisiteError, match="interfaces"):
        check_prerequisites(get_settings())


def test_disable_last_networks_user_is_refused(networks_client, seeded_db):
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    denied = networks_client.post(f"/admin/users/{nets.id}/disable", follow_redirects=False)
    assert denied.status_code == 303
    seeded_db.refresh(nets)
    assert nets.is_active is True


def test_disable_cs_user_and_audit(networks_client, seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    ok = networks_client.post(f"/admin/users/{cs.id}/disable", follow_redirects=False)
    assert ok.status_code == 303
    seeded_db.refresh(cs)
    assert cs.is_active is False
    page = networks_client.get("/admin/audit")
    assert page.status_code == 200
    assert "user_disabled" in page.text


def test_cs_cannot_open_audit(cs_client, seeded_db):
    page = cs_client.get("/admin/audit")
    assert page.status_code == 403


def test_ready_ok_in_tests(client):
    response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["db"]["ok"] is True
    assert body["poller"]["ok"] is True
    assert body["servicenow"] == "off"
    assert "password" not in response.text.lower()


def test_backup_is_readable_copy(seeded_db):
    write_poller_heartbeat()
    dest = backup_now("test")
    assert dest.is_file()
    conn = sqlite3.connect(str(dest))
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        conn.close()
    assert count >= 2
    clone = dest.with_name(dest.name + ".clone")
    sqlite_backup(dest, clone)
    assert clone.is_file()


def test_ready_report_mentions_integrations():
    report = build_ready_report()
    assert report["teams"] == "off"
    assert report["app"] == "switcheroo"
