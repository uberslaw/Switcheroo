from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.models import REQUEST_VLAN, STATUS_PENDING, ChangeRequest
from app.services.uptime import short_if_name

log = logging.getLogger("switcheroo.teams")

ALLOWED_WEBHOOK_HOSTS = {
    "outlook.office.com",
    "outlook.office365.com",
}
ALLOWED_WEBHOOK_HOST_SUFFIXES = (
    ".webhook.office.com",
    ".logic.azure.com",
    ".environment.api.powerplatform.com",
)


class TeamsError(Exception):
    pass


@dataclass
class NotifyResult:
    note: str
    payload: dict[str, Any]
    live: bool
    skipped: bool = False


def public_base_url(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    raw = (settings.public_url or "").strip().rstrip("/")
    if raw:
        return raw
    host = settings.host
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{host}:{settings.port}"


def request_page_url(request_id: int, settings: Settings | None = None) -> str:
    """Networks accept/reject lives on the Requests page for pending rows."""
    return f"{public_base_url(settings)}/requests?status=pending#request-{request_id}"


def webhook_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def validate_teams_webhook_url(url: str) -> Optional[str]:
    """Return an error message if the URL is not an https Teams/Power Automate webhook."""
    text = (url or "").strip()
    if not text:
        return "TEAMS_WEBHOOK_URL is empty."
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return "TEAMS_WEBHOOK_URL must be https."
    host = (parsed.hostname or "").lower()
    if not host:
        return "TEAMS_WEBHOOK_URL host is missing."
    if host in ALLOWED_WEBHOOK_HOSTS:
        return None
    if any(host.endswith(suffix) for suffix in ALLOWED_WEBHOOK_HOST_SUFFIXES):
        return None
    allowed = ", ".join(sorted(ALLOWED_WEBHOOK_HOSTS) + list(ALLOWED_WEBHOOK_HOST_SUFFIXES))
    return f"TEAMS_WEBHOOK_URL host {host!r} is not a Teams/Power Automate webhook ({allowed})."


def _vlan_label(vlan_id: int | None, vlan_name: str | None) -> str:
    if vlan_id is None:
        return "none"
    name = (vlan_name or "").strip()
    return f"{vlan_id} {name}".strip()


def build_notify_payload(req: ChangeRequest, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    port = req.port
    switch = req.switch
    if_name = short_if_name(port.if_name) if port is not None else f"port-{req.port_id}"
    sw_name = switch.name if switch is not None else f"switch-{req.switch_id}"
    location = switch.location if switch is not None else ""
    requester = req.requester.username if req.requester is not None else str(req.requester_id)
    frm = _vlan_label(req.from_vlan_id, req.from_vlan_name)
    to = _vlan_label(req.requested_vlan_id, req.requested_vlan_name)
    page_url = request_page_url(req.id, settings)
    title = "A VLAN change request has been generated"
    body = "Open this link to accept or reject."
    facts = [
        ("Request", f"#{req.id}"),
        ("Requester", requester),
        ("Switch", sw_name),
        ("Office", location or "—"),
        ("Port", if_name),
        ("VLAN", f"{frm} → {to}"),
    ]
    if settings.teams_webhook_format == "messagecard":
        return {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "themeColor": "C6D36E",
            "title": title,
            "text": f"{body}<br/>{sw_name} {if_name}: {frm} → {to} (requested by {requester}).",
            "sections": [
                {
                    "facts": [{"name": name, "value": value} for name, value in facts],
                }
            ],
            "potentialAction": [
                {
                    "@type": "OpenUri",
                    "name": "Open request page",
                    "targets": [{"os": "default", "uri": page_url}],
                }
            ],
        }
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.teams.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": body,
                            "wrap": True,
                        },
                        {
                            "type": "FactSet",
                            "facts": [{"title": name, "value": value} for name, value in facts],
                        },
                    ],
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open request page",
                            "url": page_url,
                        }
                    ],
                },
            }
        ],
    }


def dry_run_dir() -> Path:
    path = get_settings().data_dir / "teams-dryrun"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(request_id: int) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", f"vlan-{request_id}") + ".json"


class TeamsAdapter:
    """Incoming webhook / Workflows poster. Live HTTP only when enabled and not dry-run."""

    def __init__(self, http: httpx.Client | None = None):
        self._http = http

    def http_allowed(self) -> bool:
        return get_settings().teams_live

    def notify_vlan_pending(self, request: ChangeRequest) -> NotifyResult:
        if request.request_type != REQUEST_VLAN:
            return NotifyResult(
                note="Teams alerts are for VLAN changes only.",
                payload={},
                live=False,
                skipped=True,
            )
        if request.status != STATUS_PENDING:
            return NotifyResult(
                note="Teams alert skipped (request is not pending).",
                payload={},
                live=False,
                skipped=True,
            )
        payload = build_notify_payload(request)
        if not self.http_allowed():
            return self._dry_run_write(request, payload)
        return self._live_post(request, payload)

    def _dry_run_write(self, request: ChangeRequest, payload: dict[str, Any]) -> NotifyResult:
        settings = get_settings()
        record = {
            "mode": "dry-run",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "webhook_host": webhook_host(settings.teams_webhook_url) or "(unset — no HTTP)",
            "http": False,
            "request_id": request.id,
            "page_url": request_page_url(request.id, settings),
            "payload": payload,
        }
        path = dry_run_dir() / _safe_filename(request.id)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.info(
            "Teams dry-run notify (no HTTP) request=%s file=%s host=%s",
            request.id,
            path,
            record["webhook_host"],
        )
        return NotifyResult(
            note=f"Dry-run: would POST Teams webhook. Payload at {path}. No call to Teams.",
            payload=payload,
            live=False,
        )

    def _live_post(self, request: ChangeRequest, payload: dict[str, Any]) -> NotifyResult:
        settings = get_settings()
        err = validate_teams_webhook_url(settings.teams_webhook_url)
        if err:
            raise TeamsError(err)
        self._http_json(settings.teams_webhook_url, payload)
        log.info(
            "Teams webhook posted request=%s host=%s",
            request.id,
            webhook_host(settings.teams_webhook_url),
        )
        return NotifyResult(
            note=f"Posted Teams alert for request #{request.id}.",
            payload=payload,
            live=True,
        )

    def _http_json(self, url: str, json_body: dict[str, Any]) -> dict[str, Any]:
        if not self.http_allowed():
            raise TeamsError("Refusing Teams HTTP: TEAMS_ENABLED is false or TEAMS_DRY_RUN is true.")
        settings = get_settings()
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        timeout = httpx.Timeout(settings.teams_http_timeout, connect=min(5.0, float(settings.teams_http_timeout)))
        try:
            if self._http is not None:
                response = self._http.request("POST", url, json=json_body, headers=headers, timeout=timeout)
            elif settings.testing:
                raise TeamsError("Refusing live Teams sockets in tests. Inject a mock HTTP client.")
            else:
                with httpx.Client(timeout=timeout, verify=True) as client:
                    response = client.request("POST", url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise TeamsError(f"Teams webhook POST failed: {exc}") from exc
        if response.status_code >= 400:
            raise TeamsError(
                f"Teams webhook returned {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


teams = TeamsAdapter()
