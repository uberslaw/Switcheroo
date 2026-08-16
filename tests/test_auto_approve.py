from __future__ import annotations

from sqlalchemy import select

from app.models import REQUEST_BOUNCE, REQUEST_VLAN, Switch, User
from app.services.auto_approve import global_key, office_key, requestor_key, set_policy
from app.services.request_service import create_request
from tests.conftest import add_cs_user, first_port


def test_all_off_stays_pending(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert req.status == "pending"
    assert req.auto_approved is False
    seeded_db.refresh(port)
    assert port.vlan_id != 50


def test_global_on_vlan_auto_approves_and_writes(seeded_db):
    set_policy(seeded_db, global_key(), True)
    seeded_db.flush()
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert req.auto_approved is True
    assert req.auto_approve_reason == "Everywhere"
    assert req.status == "executed"
    assert req.reviewer_id is None
    assert "policy: global" in (req.review_note or "")
    seeded_db.refresh(port)
    assert port.vlan_id == 50
    assert port.vlan_name == "GUEST"
    assert req.servicenow_ticket == "SN-DRY-RUN"


def test_office_on_only_that_location(seeded_db):
    a = first_port(seeded_db, "CS-BLD-A-AS01")
    b = first_port(seeded_db, "CS-BLD-B-AS01")
    set_policy(seeded_db, office_key(a.switch.location), True)
    seeded_db.flush()
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req_a = create_request(seeded_db, cs, a, REQUEST_VLAN, vlan_id=50)
    req_b = create_request(seeded_db, cs, b, REQUEST_VLAN, vlan_id=40)
    seeded_db.commit()
    assert req_a.auto_approved is True
    assert req_a.auto_approve_reason.startswith("Office:")
    assert req_a.status == "executed"
    assert req_b.status == "pending"
    assert req_b.auto_approved is False
    seeded_db.refresh(a)
    seeded_db.refresh(b)
    assert a.vlan_id == 50
    assert b.vlan_id != 40


def test_requestor_on_only_that_user(seeded_db):
    limited = add_cs_user(seeded_db, "cs-limited", "cs-limited", ["CS-BLD-A-AS01"])
    set_policy(seeded_db, requestor_key(limited.id), True)
    seeded_db.flush()
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    port = first_port(seeded_db)
    req_limited = create_request(seeded_db, limited, port, REQUEST_VLAN, vlan_id=50)
    req_cs = create_request(seeded_db, cs, first_port(seeded_db, "CS-BLD-B-AS01"), REQUEST_VLAN, vlan_id=40)
    seeded_db.commit()
    assert req_limited.auto_approved is True
    assert req_limited.auto_approve_reason == "Requestor: cs-limited"
    assert req_limited.status == "executed"
    assert req_cs.status == "pending"
    assert req_cs.auto_approved is False


def test_bounce_auto_approves_when_global_on(seeded_db):
    set_policy(seeded_db, global_key(), True)
    seeded_db.flush()
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_BOUNCE)
    seeded_db.commit()
    assert req.auto_approved is True
    assert req.status == "executed"
    assert req.servicenow_ticket is None


def test_requests_page_shows_auto_approved(client, seeded_db):
    set_policy(seeded_db, global_key(), True)
    seeded_db.flush()
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/requests")
    assert page.status_code == 200
    assert "Auto-approved" in page.text
    assert "Everywhere" in page.text


def test_cs_cannot_post_policy_changes(client, seeded_db):
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    denied = client.post("/admin/policies/global", data={"enabled": "1"}, follow_redirects=False)
    assert denied.status_code == 403
    office = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-A-AS01")).location
    denied_office = client.post(
        "/admin/policies/office",
        data={"office": office, "enabled": "1"},
        follow_redirects=False,
    )
    assert denied_office.status_code == 403


def test_networks_can_toggle_policies_page(client, seeded_db):
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get("/admin/policies")
    assert page.status_code == 200
    assert "Everywhere" in page.text
    assert "Building A" in page.text
    toggle = client.post("/admin/policies/global", data={"enabled": "1"}, follow_redirects=False)
    assert toggle.status_code == 303
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert req.auto_approved is True
