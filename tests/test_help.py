from __future__ import annotations

from app.config import LAB_SECRET_KEY, get_settings
from app.security_checklist import build_security_report


def test_help_requires_login(client):
    for path in ("/help", "/help/security"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert "/login" in response.headers["location"]


def test_help_menu_and_using_page(cs_client, seeded_db):
    page = cs_client.get("/help")
    assert page.status_code == 200
    assert "Help" in page.text
    assert "Security checklist" in page.text
    assert "Using Switcheroo" in page.text
    assert "Client Services" in page.text


def test_security_checklist_page_for_cs(cs_client, seeded_db):
    page = cs_client.get("/help/security")
    assert page.status_code == 200
    assert "Already in this release" in page.text
    assert "This host still needs" in page.text
    assert "Still open" in page.text
    assert "Entra ID" in page.text
    assert "Needs action" in page.text
    assert "scrypt" in page.text
    body = page.text.lower()
    assert "change-me-lab-only" not in body
    assert get_settings().secret_key.lower() not in body


def test_security_checklist_live_flags_lab_users(seeded_db):
    report = build_security_report(seeded_db)
    host = next(section for section in report.sections if section.id == "host")
    by_id = {item.id: item for item in host.items}
    assert by_id["lab-users"].status == "action"
    assert by_id["require-hardened"].status == "action"
    assert by_id["secret-key"].status == "action"
    product = next(section for section in report.sections if section.id == "product")
    assert all(item.status == "done" for item in product.items)
    residual = next(section for section in report.sections if section.id == "residual")
    assert all(item.status == "open" for item in residual.items)
    assert report.action_count >= 1
    assert report.open_count >= 1


def test_security_checklist_hardened_host_marks_keys_done(seeded_db, monkeypatch):
    monkeypatch.setenv("SWITCHEROO_REQUIRE_HARDENED", "true")
    monkeypatch.setenv("SWITCHEROO_SECRET_KEY", "s" * 32)
    monkeypatch.setenv("SWITCHEROO_DATA_KEY", "d" * 32)
    monkeypatch.setenv("SWITCHEROO_COOKIE_SECURE", "true")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_ALLOWED_HOSTS", "switcheroo.internal.example")
    monkeypatch.setenv("SWITCHEROO_CSRF", "true")
    monkeypatch.setenv("SWITCHEROO_LOGIN_RATE_LIMIT", "true")
    report = build_security_report(seeded_db, get_settings())
    host = next(section for section in report.sections if section.id == "host")
    by_id = {item.id: item for item in host.items}
    assert by_id["require-hardened"].status == "done"
    assert by_id["secret-key"].status == "done"
    assert by_id["data-key"].status == "done"
    assert by_id["tls-cookies"].status == "done"
    assert by_id["allowed-hosts"].status == "done"
    assert by_id["csrf-on"].status == "done"
    assert by_id["lab-users"].status == "action"
    assert LAB_SECRET_KEY not in by_id["secret-key"].detail
    assert "d" * 32 not in by_id["data-key"].detail
