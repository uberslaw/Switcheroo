from __future__ import annotations


def test_help_page_explains_diagnostics(cs_client, seeded_db):
    page = cs_client.get("/help")
    assert page.status_code == 200
    assert "Using Switcheroo" in page.text
    assert "Diagnostics ON" in page.text
    assert "diagnostics.log" in page.text
    assert "Launch Control" in page.text


def test_help_hides_networks_section_from_cs(cs_client, seeded_db):
    page = cs_client.get("/help")
    assert "Networks only" not in page.text


def test_help_shows_networks_section(networks_client, seeded_db):
    page = networks_client.get("/help")
    assert page.status_code == 200
    assert "Networks only" in page.text
    assert "/admin/policies" in page.text


def test_help_requires_login(client):
    page = client.get("/help", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"


def test_footer_links_to_help(cs_client, seeded_db):
    page = cs_client.get("/")
    assert 'href="/help"' in page.text
