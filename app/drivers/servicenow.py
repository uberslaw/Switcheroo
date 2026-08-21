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
from app.diagnostics import step
from app.models import REQUEST_VLAN, ChangeRequest
from app.services.uptime import short_if_name

log = logging.getLogger("switcheroo.servicenow")

DRY_RUN_RITM = "RITM-DRY-RUN"
DRY_RUN_REQ = "REQ-DRY-RUN"
DRY_RUN_TICKET = DRY_RUN_RITM
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
    ritm_number: Optional[str] = None
    req_number: Optional[str] = None
    ritm_sys_id: Optional[str] = None
    req_sys_id: Optional[str] = None


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


def _ref_pair(ref: Any) -> tuple[Optional[str], Optional[str]]:
    """Return (sys_id, display number) from a Table API reference field."""
    if not ref:
        return None, None
    if isinstance(ref, str):
        if ref.startswith("REQ") or ref.startswith("RITM"):
            return None, ref
        return ref, None
    if isinstance(ref, dict):
        sys_id = ref.get("value") or ref.get("sys_id")
        number = ref.get("display_value") or ref.get("number")
        if isinstance(number, str) and number and not (number.startswith("REQ") or number.startswith("RITM")):
            if len(number) == 32 and " " not in number:
                sys_id = sys_id or number
                number = None
        return sys_id, number
    return None, None


def _normalize_poll_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    req_sys, req_number = _ref_pair(row.get("request"))
    if not req_number:
        req_number = row.get("request.number") or row.get("request_number")
    if req_number:
        out["req_number"] = req_number
    if req_sys:
        out["req_sys_id"] = req_sys
    if row.get("number"):
        out["ritm_number"] = row.get("number")
    return out


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
            f"windows_account: {req.windows_account or ''}",
            f"reason: {req.reason or ''}",
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
        if result.ritm_sys_id:
            request.sn_ritm_sys_id = result.ritm_sys_id
        if result.req_sys_id:
            request.sn_req_sys_id = result.req_sys_id
        if result.ritm_number:
            request.sn_ritm_number = result.ritm_number
        if result.req_number:
            request.sn_req_number = result.req_number
        if result.sys_id:
            request.servicenow_sys_id = result.sys_id
        elif result.ritm_sys_id:
            request.servicenow_sys_id = result.ritm_sys_id
        primary = result.ritm_number or result.req_number or result.number
        return primary, result.note

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
        if request.servicenow_sys_id or request.sn_ritm_sys_id:
            return TicketResult(
                number=request.sn_ritm_number or request.servicenow_ticket,
                sys_id=request.sn_ritm_sys_id or request.servicenow_sys_id,
                note="ServiceNow ticket already linked; not creating a duplicate.",
                correlation_id=corr,
                payload={},
                live=False,
                ritm_number=request.sn_ritm_number,
                req_number=request.sn_req_number,
                ritm_sys_id=request.sn_ritm_sys_id,
                req_sys_id=request.sn_req_sys_id,
            )
        payload = build_create_payload(request)
        if not self.http_allowed():
            return self._dry_run_write(request, payload, corr)
        return self._live_open(request, payload, corr)

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
        """Recover RITM/REQ numbers after restart. Dry-run reads local files only."""
        if not self.http_allowed():
            return self._dry_run_poll()
        settings = get_settings()
        query = f"correlation_idSTARTSWITH{CORRELATION_PREFIX}^ORshort_descriptionSTARTSWITH{SUBJECT_PREFIX}"
        params = {
            "sysparm_query": query,
            "sysparm_fields": "sys_id,number,correlation_id,short_description,state,request,request.number",
            "sysparm_limit": "200",
        }
        rows = self._table_query(settings.servicenow_ritm_table, params)
        if not rows and settings.servicenow_table not in {settings.servicenow_ritm_table, "sc_req_item"}:
            fallback = dict(params)
            fallback["sysparm_fields"] = "sys_id,number,correlation_id,short_description,state"
            rows = self._table_query(settings.servicenow_table, fallback)
        return [_normalize_poll_row(row) for row in rows]

    def _table_query(self, table: str, params: dict[str, str]) -> list[dict[str, Any]]:
        path = f"/api/now/table/{table}"
        data = self._http_json("GET", path, params=params)
        result = data.get("result") or []
        if isinstance(result, dict):
            return [result]
        return list(result)

    def _update_ticket(self, request: ChangeRequest, body: dict[str, Any], action: str) -> None:
        if request.request_type != REQUEST_VLAN:
            return
        if not request.servicenow_sys_id and not request.sn_ritm_sys_id and not request.servicenow_correlation_id:
            log.info("No ServiceNow link on request %s; skip %s", request.id, action)
            return
        if not self.http_allowed():
            self._dry_run_update(request, body, action)
            return
        sys_id = request.sn_ritm_sys_id or request.servicenow_sys_id
        if not sys_id:
            log.warning("Request %s has no sys_id; cannot %s live", request.id, action)
            request.servicenow_note = (request.servicenow_note or "") + f" Live {action} skipped (no sys_id)."
            return
        settings = get_settings()
        table = settings.servicenow_ritm_table if request.sn_ritm_sys_id else settings.servicenow_table
        path = f"/api/now/table/{table}/{quote(sys_id, safe='')}"
        self._http_json("PATCH", path, json_body=body)

    def _dry_run_write(self, request: ChangeRequest, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        ritm_sys = f"dryrun-ritm-{request.id}"
        req_sys = f"dryrun-req-{request.id}"
        record = {
            "mode": "dry-run",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "instance": settings.servicenow_instance_url or "(unset — no HTTP)",
            "table": settings.servicenow_ritm_table,
            "req_table": settings.servicenow_req_table,
            "http": False,
            "ritm_number": DRY_RUN_RITM,
            "req_number": DRY_RUN_REQ,
            "payload": payload,
        }
        path = dry_run_dir() / _safe_filename(corr)
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        log.info("ServiceNow dry-run create (no HTTP) request=%s file=%s payload=%s", request.id, path, payload)
        return TicketResult(
            number=DRY_RUN_RITM,
            sys_id=ritm_sys,
            note=f"Dry-run: would create {DRY_RUN_RITM} / {DRY_RUN_REQ} on {settings.servicenow_ritm_table}. Payload at {path}. No call to ServiceNow.",
            correlation_id=corr,
            payload=payload,
            live=False,
            ritm_number=DRY_RUN_RITM,
            req_number=DRY_RUN_REQ,
            ritm_sys_id=ritm_sys,
            req_sys_id=req_sys,
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
                    "sys_id": data.get("ritm_sys_id") or f"dryrun-file-{path.stem}",
                    "number": data.get("ritm_number") or DRY_RUN_RITM,
                    "ritm_number": data.get("ritm_number") or DRY_RUN_RITM,
                    "req_number": data.get("req_number") or DRY_RUN_REQ,
                    "correlation_id": payload.get("correlation_id") or "",
                    "short_description": payload.get("short_description") or "",
                    "state": "dry-run",
                }
            )
        return rows

    def _live_open(self, request: ChangeRequest, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        errors: list[str] = []
        if settings.servicenow_catalog_item_sys_id:
            try:
                return self._live_order_now(payload, corr)
            except ServiceNowError as exc:
                log.warning("Catalog order_now failed, trying Table API sc_req_item: %s", exc)
                errors.append(str(exc))
        try:
            return self._live_create_ritm(payload, corr)
        except ServiceNowError as exc:
            log.warning("sc_req_item create failed, falling back to %s: %s", settings.servicenow_table, exc)
            errors.append(str(exc))
        if settings.servicenow_table not in {settings.servicenow_ritm_table, "sc_req_item"}:
            return self._live_create_fallback(payload, corr)
        raise ServiceNowError("; ".join(errors) or "ServiceNow create failed")

    def _live_order_now(self, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        item = quote(settings.servicenow_catalog_item_sys_id, safe="")
        path = f"/api/sn_sc/servicecatalog/items/{item}/order_now"
        body = {
            "sysparm_quantity": 1,
            "variables": {
                "short_description": payload.get("short_description"),
                "description": payload.get("description"),
                "correlation_id": corr,
            },
        }
        data = self._http_json("POST", path, json_body=body)
        result = data.get("result") or {}
        if isinstance(result.get("number"), dict):
            result = {**result, **result["number"]}
        req_number = result.get("request_number") or result.get("number")
        req_sys_id = result.get("request_id") or result.get("sys_id")
        ritm_number, ritm_sys_id = self._fetch_ritm_for_request(req_sys_id, corr)
        note = f"Ordered catalog item → {ritm_number or 'RITM?'} / {req_number}."
        return TicketResult(
            number=ritm_number or req_number,
            sys_id=ritm_sys_id or req_sys_id,
            note=note,
            correlation_id=corr,
            payload=payload,
            live=True,
            ritm_number=ritm_number,
            req_number=req_number,
            ritm_sys_id=ritm_sys_id,
            req_sys_id=req_sys_id,
        )

    def _live_create_ritm(self, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        body = dict(payload)
        if settings.servicenow_catalog_item_sys_id:
            body["cat_item"] = settings.servicenow_catalog_item_sys_id
        data = self._http_json("POST", f"/api/now/table/{settings.servicenow_ritm_table}", json_body=body)
        result = data.get("result") or {}
        ritm_number = result.get("number")
        ritm_sys_id = result.get("sys_id")
        req_sys_id, req_number = _ref_pair(result.get("request"))
        if req_sys_id and not req_number:
            req_number, req_sys_id = self._fetch_request_number(req_sys_id)
        note = f"Created {settings.servicenow_ritm_table} {ritm_number} / {req_number or 'REQ?'}."
        return TicketResult(
            number=ritm_number,
            sys_id=ritm_sys_id,
            note=note,
            correlation_id=corr,
            payload=payload,
            live=True,
            ritm_number=ritm_number,
            req_number=req_number,
            ritm_sys_id=ritm_sys_id,
            req_sys_id=req_sys_id,
        )

    def _live_create_fallback(self, payload: dict[str, Any], corr: str) -> TicketResult:
        settings = get_settings()
        data = self._http_json("POST", f"/api/now/table/{settings.servicenow_table}", json_body=payload)
        result = data.get("result") or {}
        number = result.get("number")
        sys_id = result.get("sys_id")
        return TicketResult(
            number=number,
            sys_id=sys_id,
            note=f"Created fallback {settings.servicenow_table} {number} ({sys_id}).",
            correlation_id=corr,
            payload=payload,
            live=True,
        )

    def _fetch_request_number(self, req_sys_id: str) -> tuple[Optional[str], Optional[str]]:
        settings = get_settings()
        path = f"/api/now/table/{settings.servicenow_req_table}/{quote(req_sys_id, safe='')}"
        data = self._http_json("GET", path, params={"sysparm_fields": "sys_id,number"})
        result = data.get("result") or {}
        return result.get("number"), result.get("sys_id") or req_sys_id

    def _fetch_ritm_for_request(self, req_sys_id: Optional[str], corr: str) -> tuple[Optional[str], Optional[str]]:
        if not req_sys_id:
            return None, None
        settings = get_settings()
        query = f"request={req_sys_id}^ORcorrelation_id={corr}"
        rows = self._table_query(
            settings.servicenow_ritm_table,
            {
                "sysparm_query": query,
                "sysparm_fields": "sys_id,number,correlation_id,request",
                "sysparm_limit": "5",
            },
        )
        if not rows:
            return None, None
        row = rows[0]
        return row.get("number"), row.get("sys_id")

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
            with step("servicenow.call", method=method, path=path):
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
                if response.status_code >= 400:
                    raise ServiceNowError(
                        f"ServiceNow {method} {path} returned {response.status_code}: {response.text[:300]}"
                    )
        except httpx.HTTPError as exc:
            raise ServiceNowError(f"ServiceNow {method} {path} failed: {exc}") from exc
        if not response.content:
            return {}
        return response.json()


servicenow = ServiceNowAdapter()
