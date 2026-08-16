from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.drivers.servicenow import DRY_RUN_REQ, DRY_RUN_RITM, _safe_filename, build_create_payload
from app.models import REQUEST_VLAN, ChangeRequest, User
from app.services.request_service import RequestError, create_request
from tests.conftest import VLAN_REASON, first_port


def test_missing_reason_creates_nothing(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    before = seeded_db.scalar(select(func.count(ChangeRequest.id))) or 0
    folder = Path(get_settings().data_dir) / "servicenow-dryrun"
    files_before = set(folder.glob("*.json")) if folder.exists() else set()
    with pytest.raises(RequestError, match="reason"):
        create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="")
    with pytest.raises(RequestError, match="reason"):
        create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="   ")
    seeded_db.rollback()
    after = seeded_db.scalar(select(func.count(ChangeRequest.id))) or 0
    assert after == before
    files_after = set(folder.glob("*.json")) if folder.exists() else set()
    assert files_after == files_before


def test_http_blank_reason_rejected(client, seeded_db):
    port = first_port(seeded_db)
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    response = client.post(
        f"/switches/{port.switch_id}/ports/{port.id}/request/vlan",
        data={"vlan_id": "50", "reason": "  "},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "VLAN request failed" in response.text
    assert "reason" in response.text.lower()
    assert (seeded_db.scalar(select(func.count(ChangeRequest.id))) or 0) == 0


def test_reason_and_windows_account_in_payload_and_requests(client, seeded_db, monkeypatch):
    monkeypatch.setenv("USERDOMAIN", "ARUP")
    monkeypatch.setenv("USERNAME", "jsmith")
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason=VLAN_REASON)
    seeded_db.commit()
    assert req.reason == VLAN_REASON
    assert req.windows_account == r"ARUP\jsmith"
    assert req.sn_ritm_number == DRY_RUN_RITM
    assert req.sn_req_number == DRY_RUN_REQ
    payload = build_create_payload(req)
    assert "requester: cs" in payload["description"]
    assert r"windows_account: ARUP\jsmith" in payload["description"]
    assert f"reason: {VLAN_REASON}" in payload["description"]
    folder = Path(get_settings().data_dir) / "servicenow-dryrun"
    data = json.loads((folder / _safe_filename(req.servicenow_correlation_id)).read_text(encoding="utf-8"))
    assert data["ritm_number"] == DRY_RUN_RITM
    assert data["req_number"] == DRY_RUN_REQ
    assert VLAN_REASON in data["payload"]["description"]
    assert r"ARUP\jsmith" in data["payload"]["description"]

    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get("/requests")
    assert page.status_code == 200
    assert VLAN_REASON in page.text
    assert r"ARUP\jsmith" in page.text
    assert "RITM-DRY-RUN" in page.text
    assert "REQ-DRY-RUN" in page.text


def test_auto_approve_still_requires_reason(seeded_db):
    from app.services.auto_approve import global_key, set_policy

    set_policy(seeded_db, global_key(), True)
    seeded_db.flush()
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    with pytest.raises(RequestError, match="reason"):
        create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason=None)
    seeded_db.rollback()
    seeded_db.refresh(port)
    assert port.vlan_id != 50
    assert (seeded_db.scalar(select(func.count(ChangeRequest.id))) or 0) == 0


def test_approval_queue_shows_ritm_req_and_reason(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason=VLAN_REASON)
    seeded_db.commit()
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get("/admin/approvals")
    assert page.status_code == 200
    assert "RITM-DRY-RUN" in page.text
    assert "REQ-DRY-RUN" in page.text
    assert VLAN_REASON in page.text
    html = page.text
    ritm_at = html.find("RITM-DRY-RUN")
    req_at = html.find("REQ-DRY-RUN")
    assert ritm_at != -1 and req_at != -1
    assert ritm_at < req_at
