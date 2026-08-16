from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.drivers.teams import (
    TeamsAdapter,
    _safe_filename,
    build_notify_payload,
    request_page_url,
    validate_teams_webhook_url,
)
from app.models import REQUEST_BOUNCE, REQUEST_VLAN, User
from app.prereq import PrerequisiteError, check_prerequisites
from app.services.auto_approve import global_key, set_policy
from app.services.request_service import create_request
from tests.conftest import first_port

WORKFLOW_WEBHOOK = (
    "https://prod-00.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke"
    "?api-version=2016-06-01&sig=not-a-real-secret"
)


class BoomHttp:
    def request(self, *args, **kwargs):
        raise AssertionError("Teams HTTP must not run in dry-run")


def test_dry_run_create_records_payload_for_pending_vlan(seeded_db, monkeypatch):
    monkeypatch.setattr("app.drivers.teams.teams._http", BoomHttp())
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    folder = Path(get_settings().data_dir) / "teams-dryrun"
    path = folder / _safe_filename(req.id)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["http"] is False
    assert data["request_id"] == req.id
    assert data["page_url"] == f"http://switcheroo.test/requests?status=pending#request-{req.id}"
    payload = data["payload"]
    assert payload["type"] == "message"
    card = payload["attachments"][0]["content"]
    assert card["body"][0]["text"] == "A VLAN change request has been generated"
    assert "accept or reject" in card["body"][1]["text"].lower()
    assert card["actions"][0]["url"] == data["page_url"]
    blob = json.dumps(data)
    assert "not-a-real-secret" not in blob
    assert "sig=" not in blob.lower()


def test_bounce_does_not_notify_teams(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_BOUNCE)
    seeded_db.commit()
    folder = Path(get_settings().data_dir) / "teams-dryrun"
    assert not (folder / _safe_filename(req.id)).exists()


def test_auto_approve_skips_teams_alert(seeded_db):
    set_policy(seeded_db, global_key(), True)
    seeded_db.flush()
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert req.auto_approved is True
    folder = Path(get_settings().data_dir) / "teams-dryrun"
    assert not (folder / _safe_filename(req.id)).exists()


def test_no_http_when_dry_run_even_if_enabled(seeded_db, monkeypatch):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DRY_RUN", "true")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", WORKFLOW_WEBHOOK)
    adapter = TeamsAdapter(http=BoomHttp())
    assert adapter.http_allowed() is False
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    result = adapter.notify_vlan_pending(req)
    assert result.live is False
    assert result.skipped is False


def test_live_without_webhook_fails_fast(monkeypatch):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DRY_RUN", "false")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "")
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="TEAMS_WEBHOOK_URL"):
        check_prerequisites(settings)


def test_live_without_public_url_fails_fast(monkeypatch):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DRY_RUN", "false")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", WORKFLOW_WEBHOOK)
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="SWITCHEROO_PUBLIC_URL"):
        check_prerequisites(settings)


def test_rejects_non_teams_webhook_host(monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://example.com/hooks/steal")
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="not a Teams"):
        check_prerequisites(settings)


def test_rejects_http_webhook(monkeypatch):
    monkeypatch.setenv(
        "TEAMS_WEBHOOK_URL",
        "http://prod-00.westus.logic.azure.com/workflows/abc",
    )
    settings = get_settings()
    with pytest.raises(PrerequisiteError, match="https"):
        check_prerequisites(settings)


def test_validate_allows_known_hosts():
    assert validate_teams_webhook_url(WORKFLOW_WEBHOOK) is None
    assert (
        validate_teams_webhook_url("https://contoso.webhook.office.com/webhookb2/abc") is None
    )
    assert validate_teams_webhook_url("https://outlook.office.com/webhook/abc") is None
    assert (
        validate_teams_webhook_url(
            "https://default123.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/abc/triggers/manual/paths/invoke"
        )
        is None
    )


def test_payload_uses_public_url_and_request_anchor(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    url = request_page_url(req.id)
    assert url.startswith("http://switcheroo.test/requests?status=pending#request-")
    payload = build_notify_payload(req)
    assert payload["attachments"][0]["content"]["actions"][0]["url"] == url


def test_messagecard_format(seeded_db, monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_FORMAT", "messagecard")
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    payload = build_notify_payload(req)
    assert payload["@type"] == "MessageCard"
    assert payload["title"] == "A VLAN change request has been generated"
    assert payload["potentialAction"][0]["targets"][0]["uri"] == request_page_url(req.id)


class _FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200, text: str = "1"):
        self.status_code = status_code
        self._payload = payload
        self.text = text if payload is None else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeHttp:
    def __init__(self, status_code: int = 200):
        self.calls: list[tuple] = []
        self.status_code = status_code

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("json")))
        return _FakeResponse(status_code=self.status_code)


def test_live_create_posts_webhook_when_mocked(seeded_db, monkeypatch):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DRY_RUN", "false")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", WORKFLOW_WEBHOOK)
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal")
    fake = FakeHttp()
    monkeypatch.setattr("app.drivers.teams.teams._http", fake)
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert fake.calls
    method, url, body = fake.calls[0]
    assert method == "POST"
    assert url == WORKFLOW_WEBHOOK
    assert body["type"] == "message"
    assert "https://switcheroo.internal/requests?status=pending#request-" in json.dumps(body)
    assert "password" not in json.dumps(body).lower()


def test_live_http_failure_does_not_drop_request(seeded_db, monkeypatch):
    monkeypatch.setenv("TEAMS_ENABLED", "true")
    monkeypatch.setenv("TEAMS_DRY_RUN", "false")
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", WORKFLOW_WEBHOOK)
    monkeypatch.setenv("SWITCHEROO_PUBLIC_URL", "https://switcheroo.internal")
    fake = FakeHttp(status_code=500)
    monkeypatch.setattr("app.drivers.teams.teams._http", fake)
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    assert req.id is not None
    assert req.status == "pending"
    assert fake.calls
