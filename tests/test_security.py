from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import select

from app.auth import hash_password, verify_password
from app.config import LAB_SECRET_KEY, get_settings
from app.crypto import PREFIX, SecretError, reveal_secret, store_secret
from app.models import Switch, User
from app.prereq import PrerequisiteError, check_prerequisites
from app.rate_limit import reset_login_failures_for_tests


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
