from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.models import REQUEST_VLAN, ChangeRequest
from app.services.uptime import short_if_name

log = logging.getLogger("switcheroo.servicenow")

DRY_RUN_TICKET = "SN-DRY-RUN"
CORRELATION_PREFIX = "switcheroo:vlan:"
SUBJECT_PREFIX = "[Switcheroo]"


@dataclass
class TicketResult:
    number: Optional[str]
    sys_id: Optional[str]
    note: str
    correlation_id: str
    payload: dict[str, Any]
    live: bool


class ServiceNowError(Exception):
    pass


def correlation_id_for(request_id: int) -> str:
    return f"{CORRELATION_PREFIX}{request_id}"


def vlan_short_description(req: ChangeRequest) -> str:
    port = req.port
    switch = req.switch
    if_name = short_if_name(port.if_name) if port is not None else f"port-{req.port_id}"
    sw_name = switch.name if switch is not None else f"switch-{req.switch_id}"
    frm = _vlan_label(req.from_vlan_id, req.from_vlan_name)
    to = _vlan_label(req.requested_vlan_id, req.requested_vlan_name)
    return f"{SUBJECT_PREFIX} VLAN {if_name} {sw_name} {frm} -> {to}"


def _vlan_label(vlan_id: int | None, vlan_name: str | None) -> str:
    if vlan_id is None:
        return "none"
    name = (vlan_name or "").strip()
    return f"{vlan_id} {name}".strip()


def build_create_payload(req: ChangeRequest) -> dict[str, Any]:
    settings = get_settings()
    corr = req.servicenow_correlation_id or correlation_id_for(req.id)
    port = req.port
    switch = req.switch
    requester = req.requester.username if req.requester is not None else str(req.requester_id)
    description = "\n".join(
        [
            "Switcheroo VLAN change request (Networks must approve in Switcheroo before the port is written).",
            f"correlation_id: {corr}",
            f"request_id: {req.id}",
            f"requester: {requester}",
            f"switch: {switch.name if switch else req.switch_id}",
            f"location/office: {switch.location if switch else ''}",
            f"management_ip: {switch.management_ip if switch else ''}",
            f"port: {port.if_name if port else req.port_id}",
            f"from_vlan: {_vlan_label(req.from_vlan_id, req.from_vlan_name)}",
            f"to_vlan: {_vlan_label(req.requested_vlan_id, req.requested_vlan_name)}",
        ]
    )
    body: dict[str, Any] = {
        "short_description": vlan_short_description(req),
        "description": description,
        "correlation_id": corr,
        "correlation_display": "Switcheroo",
    }
    if settings.servicenow_assignment_group:
        body["assignment_group"] = settings.servicenow_assignment_group
    return body


def dry_run_dir() -> Path:
    path = get_settings().data_dir / "servicenow-dryrun"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(correlation_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", correlation_id) + ".json"


class ServiceNowAdapter:
    """VLAN-change Table API client. Live HTTP only when enabled and not dry-run."""

    def __init__(self, http: httpx.Client | None = None):
        self._http = http

    def http_allowed(self) -> bool:
        return get_settings().servicenow_live

    def create_ticket(self, request: ChangeRequest) -> tuple[Optional[str], str]:
        """Keep the request_service contract. VLAN only; bounce/refresh skip SN."""
        result = self.open_vlan_incident(request)
        if result.correlation_id:
            request.servicenow_correlation_id = result.correlation_id
        if result.sys_id:
            request.servicenow_sys_id = result.sys_id
        return result.number, result.note

    def open_vlan_incident(self, request: ChangeRequest) -> TicketResult:
        if request.request_type != REQUEST_VLAN:
            return TicketResult(
                number=None,
                sys_id=None,
                note="ServiceNow POC creates tickets for VLAN changes only.",
                correlation_id="",
                payload={},
                live=False,
            )
        corr = request.servicenow_correlation_id or correlation_id_for(request.id)
        request.servicenow_correlation_id = corr
        if request.servicenow_sys_id:
            return TicketResult(
                number=request.servicenow_ticket,
                sys_id=request.servicenow_sys_id,
                note="ServiceNow ticket already linked; not creating a duplicate.",
                correlation_id=corr,
                payload={},
                live=False,
            )
        payload = build_create_payload(request)
        if not self.http_allowed():
            return self._dry_run_write(request, payload, corr)
        return self._live_create(payload, corr)

    def resolve_ticket(self, request: ChangeRequest, work_notes: str) -> None:
        settings = get_settings()
        body = {
            "state": settings.servicenow_state_resolved,
            "close_code": settings.servicenow_close_code,
            "close_notes": work_notes,
            "work_notes": work_notes,
        }
        self._update_ticket(request, body, "resolve")

    def cancel_ticket(self, request: ChangeRequest, work_notes: str) -> None:
        settings = get_settings()
        body = {
            "state": settings.servicenow_state_cancelled,
            "work_notes": work_notes,
            "close_notes": work_notes,
        }
        self._update_ticket(request, body, "cancel")

    def poll_open_tickets(self) -> list[dict[str, Any]]:
        """Recover ticket numbers after restart. Dry-run reads local files only."""
        if not self.http_allowed():
            return self._dry_run_poll()
        settings = get_settings()
        query = f"correlation_idSTARTSWITH{CORRELATION_PREFIX}^ORshort_descriptionSTARTSWITH{SUBJECT_PREFIX}"
        path = f"/api/now/table/{settings.servicenow_table}"
        params = {
            "sysparm_query": query,
            "sysparm_fields": "sys_id,number,correlation_id,short_description,state",
            "sysparm_limit": "200",
        }
        data = self._http_json("GET", path, params=params)
        result = data.get("result") or []
        if isinstance(result, dict):
            return [result]
        return list(result)

    def _update_ticket(self, request: ChangeRequest, body: dict[str, Any], action: str) -> None:
        if request.request_type != REQUEST_VLAN:
            return
        if not request.servicenow_sys_id and not request.servicenow_correlation_id:
            log.info("No ServiceNow link on request %s; skip %s", request.id, action)
            return
        if not self.http_allowed():
            self._dry_run_update(request, body, action)
            return
        if not request.servicenow_sys_id:
            log.warning("Request %s has no sys_id; cannot %s live", request.id, action)
            request.servicenow_note = (request.servicenow_note or "") + f" Live {action} skipped (no sys_id)."
            return
        settings = get_settings()
        path = f"/api/now/table/{settings.servicenow_table}/{quote(request.servicenow_sys_id, safe='')}"
        self._http_json("PATCH", path, json_body=body)

    def _dry_run_write(self, request: ChangeRequest, payload: dict[str, Any], corr: str) -> TicketResult:
        record = {
            "mode": "dry-run",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instance": get_settings().servicenow_instance_url or "(unset — no HTTP)",
            "table": get_settings().servicenow_table,
            "http": False,
            "payload": payload,
        }
        path = dry_run_dir() / _safe_filename(corr)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.info("ServiceNow dry-run create (no HTTP) request=%s file=%s payload=%s", request.id, path, payload)
        sys_id = f"dryrun-{request.id}"
        return TicketResult(
            number=DRY_RUN_TICKET,
            sys_id=sys_id,
            note=f"Dry-run: would POST {get_settings().servicenow_table}. Payload at {path}. No call to ServiceNow.",
            correlation_id=corr,
            payload=payload,
            live=False,
        )

    def _dry_run_update(self, request: ChangeRequest, body: dict[str, Any], action: str) -> None:
        corr = request.servicenow_correlation_id or correlation_id_for(request.id)
        path = dry_run_dir() / _safe_filename(corr)
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = {}
        existing["last_update"] = {
            "action": action,
            "at": datetime.now(timezone.utc).isoformat(),
            "http": False,
            "body": body,
        }
        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        log.info("ServiceNow dry-run %s (no HTTP) request=%s file=%s", action, request.id, path)
        request.servicenow_note = (request.servicenow_note or "") + f" Dry-run {action} recorded."

    def _dry_run_poll(self) -> list[dict[str, Any]]:
        folder = dry_run_dir()
        rows: list[dict[str, Any]] = []
        for path in folder.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            payload = data.get("payload") or {}
            rows.append(
                {
                    "sys_id": f"dryrun-file-{path.stem}",
                    "number": DRY_RUN_TICKET,
                    "correlation_id": payload.get("correlation_id") or "",
                    "short_description": payload.get("short_description") or "",
                    "state": "dry-run",
                }
            )
        return rows

    def _live_create(self, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        path = f"/api/now/table/{settings.servicenow_table}"
        data = self._http_json("POST", path, json_body=payload)
        result = data.get("result") or {}
        number = result.get("number")
        sys_id = result.get("sys_id")
        return TicketResult(
            number=number,
            sys_id=sys_id,
            note=f"Created {settings.servicenow_table} {number} ({sys_id}).",
            correlation_id=corr,
            payload=payload,
            live=True,
        )

    def _http_json(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.http_allowed():
            raise ServiceNowError("Refusing ServiceNow HTTP: SERVICENOW_ENABLED is false or DRY_RUN is true.")
        settings = get_settings()
        if not settings.servicenow_instance_url or not settings.servicenow_username or not settings.servicenow_password:
            raise ServiceNowError("Refusing ServiceNow HTTP: instance or credentials missing.")
        url = settings.servicenow_instance_url.rstrip("/") + path
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        auth = (settings.servicenow_username, settings.servicenow_password)
        timeout = httpx.Timeout(settings.servicenow_http_timeout, connect=min(5.0, float(settings.servicenow_http_timeout)))
        try:
            if self._http is not None:
                response = self._http.request(
                    method, url, json=json_body, params=params, headers=headers, auth=auth, timeout=timeout
                )
            elif settings.testing:
                raise ServiceNowError(
                    "Refusing live ServiceNow sockets in tests. Inject a mock HTTP client."
                )
            else:
                with httpx.Client(timeout=timeout, verify=True) as client:
                    response = client.request(method, url, json=json_body, params=params, headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            raise ServiceNowError(f"ServiceNow {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise ServiceNowError(
                f"ServiceNow {method} {path} returned {response.status_code}: {response.text[:300]}"
            )
        if not response.content:
            return {}
        return response.json()


servicenow = ServiceNowAdapter()
