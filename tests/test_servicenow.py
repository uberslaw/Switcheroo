from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.drivers.servicenow import (
    DRY_RUN_REQ,
    DRY_RUN_RITM,
    DRY_RUN_TICKET,
    ServiceNowAdapter,
    _safe_filename,
    build_create_payload,
    vlan_short_description,
)
from app.models import REQUEST_BOUNCE, REQUEST_VLAN, User
from app.prereq import PrerequisiteError, check_prerequisites
from app.services.request_service import approve_request, create_request, reject_request
from tests.conftest import first_port


class BoomHttp:
    def request(self, *args, **kwargs):
        raise AssertionError("ServiceNow HTTP must not run in dry-run")


def test_dry_run_create_records_payload_and_local_request(seeded_db, monkeypatch):
    monkeypatch.setattr("app.drivers.servicenow.servicenow._http", BoomHttp())
    port = first_port(seeded_db)
    from_vlan = port.vlan_id
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    assert req.servicenow_ticket == DRY_RUN_TICKET
    assert req.sn_ritm_number == DRY_RUN_RITM
    assert req.sn_req_number == DRY_RUN_REQ
    assert req.servicenow_sys_id
    assert req.servicenow_correlation_id == f"switcheroo:vlan:{req.id}"
    assert req.from_vlan_id == from_vlan
    folder = Path(get_settings().data_dir) / "servicenow-dryrun"
    path = folder / _safe_filename(req.servicenow_correlation_id)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["http"] is False
    assert data["ritm_number"] == DRY_RUN_RITM
    assert data["req_number"] == DRY_RUN_REQ
    assert data["payload"]["correlation_id"] == req.servicenow_correlation_id
    assert "50 GUEST" in data["payload"]["short_description"]
    desc = data["payload"]["description"]
    assert "Need guest VLAN for visitor laptop" in desc
    assert "requester: cs" in desc
    blob = json.dumps(data).lower()
    assert "password" not in blob or "servicenow_password" not in blob


def test_dry_run_create_is_idempotent(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.flush()
    first_sys = req.servicenow_sys_id
    ticket, note = ServiceNowAdapter(http=BoomHttp()).create_ticket(req)
    assert ticket == DRY_RUN_TICKET
    assert req.servicenow_sys_id == first_sys
    assert "duplicate" in note.lower() or "already" in note.lower()


def test_bounce_does_not_create_sn_ticket(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_BOUNCE)
    seeded_db.commit()
    assert req.servicenow_ticket is None


def test_approve_and_reject_update_status_and_dry_run_file(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.flush()
    approve_request(seeded_db, req, nets, "ok")
    seeded_db.commit()
    assert req.status == "executed"
    folder = Path(get_settings().data_dir) / "servicenow-dryrun"
    data = json.loads((folder / _safe_filename(req.servicenow_correlation_id)).read_text(encoding="utf-8"))
    assert data.get("last_update", {}).get("action") == "resolve"

    port2 = first_port(seeded_db)
    req2 = create_request(seeded_db, cs, port2, REQUEST_VLAN, vlan_id=40, reason="Need guest VLAN for visitor laptop")
    seeded_db.flush()
    reject_request(seeded_db, req2, nets, "not now")
    seeded_db.commit()
    assert req2.status == "rejected"


def test_no_http_when_dry_run_even_if_enabled(seeded_db, monkeypatch):
    monkeypatch.setenv("SERVICENOW_ENABLED", "true")
    monkeypatch.setenv("SERVICENOW_DRY_RUN", "true")
    monkeypatch.setenv("SERVICENOW_INSTANCE", "https://arup.service-now.com")
    adapter = ServiceNowAdapter(http=BoomHttp())
    assert adapter.http_allowed() is False
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    result = adapter.open_vlan_incident(req)
    assert result.live is False
    assert result.number == DRY_RUN_TICKET


def test_live_without_credentials_fails_fast(monkeypatch):
    monkeypatch.setenv("SERVICENOW_ENABLED", "true")
    monkeypatch.setenv("SERVICENOW_DRY_RUN", "false")
    monkeypatch.setenv("SERVICENOW_INSTANCE", "https://arup.service-now.com")
    monkeypatch.setenv("SERVICENOW_USERNAME", "")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="SERVICENOW_USERNAME"):
        check_prerequisites(settings)


def test_short_description_template(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.flush()
    text = vlan_short_description(req)
    assert text.startswith("[Switcheroo] VLAN")
    assert "->" in text
    payload = build_create_payload(req)
    assert payload["correlation_id"].startswith("switcheroo:vlan:")


def test_poll_is_noop_http_in_dry_run(seeded_db):
    adapter = ServiceNowAdapter(http=BoomHttp())
    rows = adapter.poll_open_tickets()
    assert isinstance(rows, list)
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    rows = adapter.poll_open_tickets()
    assert any(r.get("correlation_id") == req.servicenow_correlation_id for r in rows)
    assert all(r.get("number") == DRY_RUN_RITM for r in rows)
    assert all(r.get("req_number") == DRY_RUN_REQ for r in rows)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 201):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self):
        self.calls: list[tuple] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json"), kwargs.get("params")))
        target = str(url)
        if method == "POST" and "order_now" in target:
            return _FakeResponse({"result": {"number": "REQ0010001", "sys_id": "req-sys", "request_number": "REQ0010001"}})
        if method == "POST" and "sc_req_item" in target:
            return _FakeResponse(
                {
                    "result": {
                        "number": "RITM0012345",
                        "sys_id": "ritm-sys",
                        "request": {"value": "req-sys", "display_value": "REQ0010001"},
                    }
                }
            )
        if method == "GET" and "sc_req_item" in target:
            return _FakeResponse(
                {
                    "result": [
                        {
                            "number": "RITM0012345",
                            "sys_id": "ritm-sys",
                            "correlation_id": "switcheroo:vlan:1",
                            "request": {"value": "req-sys", "display_value": "REQ0010001"},
                        }
                    ]
                },
                status_code=200,
            )
        if method == "GET" and "sc_request" in target:
            return _FakeResponse({"result": {"number": "REQ0010001", "sys_id": "req-sys"}}, status_code=200)
        if method == "POST":
            return _FakeResponse({"result": {"number": "INC0012345", "sys_id": "abc123sys"}})
        if method == "GET":
            return _FakeResponse({"result": []}, status_code=200)
        return _FakeResponse({"result": {}}, status_code=200)


def test_live_create_posts_table_api_when_mocked(seeded_db, monkeypatch):
    monkeypatch.setenv("SERVICENOW_ENABLED", "true")
    monkeypatch.setenv("SERVICENOW_DRY_RUN", "false")
    monkeypatch.setenv("SERVICENOW_INSTANCE", "https://arup.service-now.com")
    monkeypatch.setenv("SERVICENOW_USERNAME", "intg.switcheroo")
    monkeypatch.setenv("SERVICENOW_PASSWORD", "not-a-real-password")
    fake = FakeHttp()
    monkeypatch.setattr("app.drivers.servicenow.servicenow._http", fake)
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.commit()
    assert req.sn_ritm_number == "RITM0012345"
    assert req.sn_req_number == "REQ0010001"
    assert req.servicenow_ticket == "RITM0012345"
    assert req.servicenow_sys_id == "ritm-sys"
    assert fake.calls
    method, url, body, _params = fake.calls[0]
    assert method == "POST"
    assert url.endswith("/api/now/table/sc_req_item")
    assert "arup.service-now.com" in url
    assert body["correlation_id"] == req.servicenow_correlation_id
    assert "Need guest VLAN for visitor laptop" in body["description"]
    assert "password" not in json.dumps(body).lower()


def test_reject_records_dry_run_cancel(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50, reason="Need guest VLAN for visitor laptop")
    seeded_db.flush()
    reject_request(seeded_db, req, nets, "not now")
    seeded_db.commit()
    folder = Path(get_settings().data_dir) / "servicenow-dryrun"
    data = json.loads((folder / _safe_filename(req.servicenow_correlation_id)).read_text(encoding="utf-8"))
    assert data.get("last_update", {}).get("action") == "cancel"
