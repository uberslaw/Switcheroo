from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import select

from app.auth import authenticate, hash_password, verify_password
from app.config import LAB_SECRET_KEY, get_settings
from app.crypto import PREFIX, SecretError, reveal_secret, store_secret
from app.models import Switch, User
from app.prereq import PrerequisiteError, check_prerequisites
from app.rate_limit import reset_login_failures_for_tests
from app.seed import ensure_hardened_users, seed


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "login page must include a CSRF hidden field"
    return match.group(1)


def test_user_passwords_are_scrypt_not_plaintext(seeded_db):
    user = seeded_db.scalar(select(User).where(User.username == "cs"))
    assert user.password_hash.startswith("scrypt$")
    assert user.password_hash != "cs"
    assert verify_password("cs", user.password_hash)
    assert not verify_password("wrong", user.password_hash)


def test_legacy_pbkdf2_hashes_still_verify():
    salt = bytes.fromhex("00" * 16)
    digest = hashlib.pbkdf2_hmac("sha256", b"legacy-pass", salt, 200_000)
    stored = f"pbkdf2${salt.hex()}${digest.hex()}"
    assert verify_password("legacy-pass", stored)
    assert not verify_password("nope", stored)
    assert hash_password("x").startswith("scrypt$")


def test_device_secret_round_trip_is_not_plaintext():
    stored = store_secret("tacacs-secret")
    assert stored is not None
    assert stored.startswith(PREFIX)
    assert "tacacs-secret" not in stored
    assert reveal_secret(stored) == "tacacs-secret"


def test_device_secret_wrong_key_fails(monkeypatch):
    stored = store_secret("tacacs-secret")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "some-other-key-that-is-long-enough")
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "")
    with pytest.raises(SecretError):
        reveal_secret(stored)


def test_admin_stores_encrypted_device_password(client, seeded_db):
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    response = client.post(
        "/admin/switches/new",
        data={
            "name": "LAB-SEC-AS01",
            "management_ip": "192.0.2.99",
            "location": "lab",
            "notes": "",
            "username": "tacacs.switcheroo",
            "password": "super-secret-tacacs",
            "driver_override": "simulator",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    seeded_db.expire_all()
    switch = seeded_db.scalar(select(Switch).where(Switch.name == "LAB-SEC-AS01"))
    assert switch is not None
    seeded_db.refresh(switch)
    assert switch.password
    assert switch.password.startswith(PREFIX)
    assert "super-secret-tacacs" not in switch.password
    assert reveal_secret(switch.password) == "super-secret-tacacs"


def test_security_headers_present(client):
    response = client.get("/login")
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert "frame-ancestors 'none'" in (response.headers.get("content-security-policy") or "")


def test_csrf_rejects_login_without_token(client, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_CSRF", "true")
    page = client.get("/login")
    assert page.status_code == 200
    denied = client.post(
        "/login",
        data={"username": "networks", "password": "networks"},
        follow_redirects=False,
    )
    assert denied.status_code == 403
    token = _csrf_from(page.text)
    ok = client.post(
        "/login",
        data={"username": "networks", "password": "networks", "csrf_token": token},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_login_rate_limit_locks_out(client, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_LOGIN_RATE_LIMIT", "true")
    reset_login_failures_for_tests()
    for _ in range(8):
        response = client.post(
            "/login",
            data={"username": "networks", "password": "wrong"},
            follow_redirects=False,
        )
        assert response.status_code == 401
    blocked = client.post(
        "/login",
        data={"username": "networks", "password": "wrong"},
        follow_redirects=False,
    )
    assert blocked.status_code == 429
    reset_login_failures_for_tests()


def test_refuses_open_bind_with_lab_secret(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_TESTING", "0")
    monkeypatch.setenv("SWITCHEROO_HOST", "0.0.0.0")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", LAB_SECRET_KEY)
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "false")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="0.0.0.0"):
        check_prerequisites(settings)


def test_require_hardened_rejects_lab_secret(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", LAB_SECRET_KEY)
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="SWITCHEROO_SECRET_KEY"):
        check_prerequisites(settings)


def test_health_does_not_echo_secrets(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.text.lower()
    assert "password" not in body
    assert "secret" not in body
    assert "webhook" not in body


def test_login_rotates_session_cookie(client, seeded_db):
    client.get("/login")
    before = client.cookies.get("session")
    assert before
    response = client.post(
        "/login",
        data={"username": "cs", "password": "cs"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    after = client.cookies.get("session")
    assert after
    assert after != before


def test_unknown_user_still_hashes_password(seeded_db, monkeypatch):
    calls = {"n": 0}
    original = verify_password

    def wrapped(password: str, stored: str) -> bool:
        calls["n"] += 1
        return original(password, stored)

    monkeypatch.setattr("app.auth.verify_password", wrapped)
    assert authenticate(seeded_db, "no-such-user", "whatever") is None
    assert calls["n"] == 1


def test_html_is_not_cached(client):
    response = client.get("/login")
    assert response.headers.get("cache-control") == "no-store"


def test_hsts_when_secure_cookies(client, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    response = client.get("/login")
    assert "max-age=" in (response.headers.get("strict-transport-security") or "")


def test_trusted_host_rejects_unknown_host(client):
    denied = client.get("/health", headers={"host": "evil.example"})
    assert denied.status_code == 400
    ok = client.get("/health")
    assert ok.status_code == 200


def test_require_hardened_requires_allowed_hosts(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "d" * 32)
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_ALLOWED_HOSTS", "")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="ALLOWED_HOSTS"):
        check_prerequisites(settings)


def test_require_hardened_allowed_hosts_must_match_public_url(monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "d" * 32)
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_ALLOWED_HOSTS", "localhost")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="switcheroo.internal.example"):
        check_prerequisites(settings)


def test_hardened_seed_skips_lab_users(db, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_BOOTSTRAP_PASSWORD", "")
    result = seed(db)
    assert result["users"] == 0
    assert result["switches"] == 0
    assert db.scalar(select(User).where(User.username == "networks")) is None
    with pytest.raises(PrerequisiteError, match="no users"):
        ensure_hardened_users(db)


def test_hardened_seed_bootstraps_admin(db, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_BOOTSTRAP_USERNAME", "netadmin")
    monkeypatch.setenv("SWITCHEROO_BOOTSTRAP_PASSWORD", "a-long-enough-bootstrap")
    result = seed(db)
    assert result["users"] == 1
    user = db.scalar(select(User).where(User.username == "netadmin"))
    assert user is not None
    assert user.role == "networks"
    assert verify_password("a-long-enough-bootstrap", user.password_hash)
    assert db.scalar(select(User).where(User.username == "cs")) is None
    ensure_hardened_users(db)


def test_bootstrap_password_too_short(db, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_BOOTSTRAP_PASSWORD", "short")
    with pytest.raises(PrerequisiteError, match="BOOTSTRAP_PASSWORD"):
        seed(db)


def test_admin_rejects_short_password(client, seeded_db):
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    response = client.post(
        "/admin/users",
        data={"username": "newbie", "password": "short", "role": "cs", "display_name": "n"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert seeded_db.scalar(select(User).where(User.username == "newbie")) is None


def test_audit_log_records_login_without_password(client, seeded_db):
    client.post("/login", data={"username": "networks", "password": "wrong"}, follow_redirects=False)
    path = get_settings().data_dir / "audit.log"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "login_failure" in text
    assert "wrong" not in text
    assert "password" not in text
    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_sqlite_file_mode_is_private():
    from pathlib import Path

    from app.db import init_db
    from app.filesec import sqlite_filesystem_path

    init_db()
    path = sqlite_filesystem_path(get_settings().database_url)
    assert path is not None
    assert Path(path).is_file()
    assert path.stat().st_mode & 0o777 == 0o600


def test_login_page_hides_lab_passwords_when_hardened(client, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    page = client.get("/login")
    assert "networks</code> / <code>networks" not in page.text


def test_login_page_shows_lab_passwords_in_lab_mode(client, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", LAB_SECRET_KEY)
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "false")
    page = client.get("/login")
    assert "networks</code> / <code>networks" in page.text
