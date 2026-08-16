from __future__ import annotations

from sqlalchemy import select

from app.models import REQUEST_VLAN, User
from app.services.request_service import approve_request, create_request, reject_request
from tests.conftest import first_port


def test_pending_vlan_appears_in_pane_status(client, seeded_db):
    port = first_port(seeded_db)
    current = port.vlan_id
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()

    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    response = client.get(f"/partials/switches/{port.switch_id}/pane-status?port={port.id}")
    assert response.status_code == 200
    html = response.text
    assert str(current) in html
    assert "USER" in html or str(current) in html
    assert "50" in html
    assert "GUEST" in html
    assert "REQ" in html
    assert 'name="vlan_id"' not in html


def test_pending_vlan_cleared_after_reject(client, seeded_db):
    port = first_port(seeded_db)
    current = port.vlan_id
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    reject_request(seeded_db, req, nets, "keep current vlan")
    seeded_db.commit()

    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    html = client.get(f"/partials/switches/{port.switch_id}/pane-status?port={port.id}").text
    assert "REQ" not in html
    assert str(current) in html
    seeded_db.refresh(port)
    assert port.vlan_id == current


def test_pending_vlan_cleared_after_approve(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    approve_request(seeded_db, req, nets, "ok")
    seeded_db.commit()

    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    html = client.get(f"/partials/switches/{port.switch_id}/pane-status?port={port.id}").text
    assert "REQ" not in html
    assert "50" in html
    assert "GUEST" in html
    seeded_db.refresh(port)
    assert port.vlan_id == 50


def test_vlan_htmx_post_returns_toast(client, seeded_db):
    port = first_port(seeded_db)
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    response = client.post(
        f"/switches/{port.switch_id}/ports/{port.id}/request/vlan",
        data={"vlan_id": "50"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "VLAN Change Requested" in response.text
    assert response.headers.get("HX-Trigger") == "refreshPaneStatus"


def test_cs_pane_includes_action_buttons(client):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/switches/1?port=1")
    assert page.status_code == 200
    assert 'id="pane-actions"' in page.text
    assert "Request VLAN change" in page.text
    assert "Refresh" in page.text
    assert "Troubleshoot" in page.text
    assert "Bounce" in page.text or "Bring online" in page.text
    assert "Shut the port down and bring it back up" in page.text or "Bring online" in page.text
