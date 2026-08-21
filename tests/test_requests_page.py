from __future__ import annotations

from sqlalchemy import select

from app.models import REQUEST_VLAN, Switch, User
from app.services.request_service import create_request
from tests.conftest import add_cs_user, first_port


def test_networks_sees_all_offices(client, seeded_db):
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    a = first_port(seeded_db, "CS-BLD-A-AS01")
    b_switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    from app.models import Port

    b = seeded_db.scalar(select(Port).where(Port.switch_id == b_switch.id).order_by(Port.if_index))
    create_request(seeded_db, cs, a, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    create_request(seeded_db, cs, b, REQUEST_VLAN, vlan_id=40, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get("/requests")
    assert page.status_code == 200
    assert "CS-BLD-A-AS01" in page.text
    assert "CS-BLD-B-AS01" in page.text
    assert "RITM-DRY-RUN" in page.text
    assert "REQ-DRY-RUN" in page.text
    assert "Requests" in page.text


def test_cs_limited_only_sees_permitted_switch(client, seeded_db, closed_access):
    add_cs_user(seeded_db, "cs-limited", "cs-limited", ["CS-BLD-A-AS01"])
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    a = first_port(seeded_db, "CS-BLD-A-AS01")
    b_switch = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    from app.models import Port

    b = seeded_db.scalar(select(Port).where(Port.switch_id == b_switch.id).order_by(Port.if_index))
    create_request(seeded_db, cs, a, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    create_request(seeded_db, cs, b, REQUEST_VLAN, vlan_id=40, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    client.post("/login", data={"username": "cs-limited", "password": "cs-limited"}, follow_redirects=False)
    page = client.get("/requests")
    assert page.status_code == 200
    assert "CS-BLD-A-AS01" in page.text
    assert "CS-BLD-B-AS01" not in page.text
    assert "Approval queue" not in page.text
    assert "Approve &amp; run" not in page.text
    assert "Approve & run" not in page.text
    pending = client.get("/requests?status=pending")
    assert pending.status_code == 200
    assert "CS-BLD-A-AS01" in pending.text
    assert "CS-BLD-B-AS01" not in pending.text


def test_networks_can_approve_from_requests_page(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get("/requests?status=pending")
    assert page.status_code == 200
    assert f'id="request-{req.id}"' in page.text
    assert "Approve &amp; run" in page.text
    assert f"/admin/approvals/{req.id}/approve" in page.text
    response = client.post(
        f"/admin/approvals/{req.id}/approve",
        data={"note": "ok", "next": "/requests?status=pending"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/requests?status=pending"
    seeded_db.refresh(req)
    assert req.status == "executed"
    seeded_db.refresh(port)
    assert port.vlan_id == 50


def test_networks_reject_from_requests_ignores_external_next(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    response = client.post(
        f"/admin/approvals/{req.id}/reject",
        data={"note": "not now", "next": "https://evil.example/phish"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/approvals"
    seeded_db.refresh(req)
    assert req.status == "rejected"


def test_pending_vlan_pane_shows_dry_run_ticket(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    html = client.get(f"/partials/switches/{port.switch_id}/pane-status?port={port.id}").text
    assert "REQ" in html
    assert "RITM-DRY-RUN" in html
    assert "50" in html
