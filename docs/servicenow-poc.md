# ServiceNow integration brief (give this to ServiceNow / IAM)

Switcheroo is an internal Client Services / Networks website. People request **VLAN changes** there. ServiceNow is the **ticket log**, not the place staff click to change a port.

This is a **POC**. Defaults stay dry-run. No live call is made to `https://arup.service-now.com` until `SERVICENOW_ENABLED=true`, `SERVICENOW_DRY_RUN=false`, and a dedicated integration user is set.

## What to request

| Item | Value |
| --- | --- |
| Instance | `https://arup.service-now.com` |
| Account type | **Dedicated integration user** (not a personal login, not a CS/Networks named user) |
| Protocol | HTTPS Table API, **HTTP Basic** (username + password) |
| Table (POC) | `incident` (configurable; confirm if Arup prefers `sc_req_item` / change) |
| Source | Internal server process (Switcheroo). Not a browser user. Provide the app host’s **egress IP** so SN/IAM can allowlist if required. |
| Roles (typical — confirm with SN) | `rest_service` plus Table API ACL on `incident`: **create, read, write** for that user |

Do **not** grant the integration user a human desktop role unless SN requires it for Table API.

## Create (CS VLAN request)

`POST /api/now/table/incident`

```json
{
  "short_description": "[Switcheroo] VLAN Gi1/0/11 CS-BLD-A-AS01 10 USER -> 50 GUEST",
  "description": "Switcheroo VLAN change request (Networks must approve in Switcheroo before the port is written).\ncorrelation_id: switcheroo:vlan:42\nrequest_id: 42\nrequester: cs\nswitch: CS-BLD-A-AS01\nlocation/office: Building A / IDF-1 (simulated)\nmanagement_ip: 192.0.2.10\nport: GigabitEthernet1/0/11\nfrom_vlan: 10 USER\nto_vlan: 50 GUEST",
  "correlation_id": "switcheroo:vlan:42",
  "correlation_display": "Switcheroo"
}
```

`assignment_group` is sent only if `SERVICENOW_ASSIGNMENT_GROUP` is set. Confirm the group sys_id or name with the SN team.

## Poll (restart recovery, every 1–5 minutes)

`GET /api/now/table/incident?sysparm_query=correlation_idSTARTSWITHswitcheroo:^ORshort_descriptionSTARTSWITH[Switcheroo]&sysparm_fields=sys_id,number,correlation_id,short_description,state&sysparm_limit=200`

This is **not** per-port and does not walk campus switches.

## Resolve (Networks Approve + port write succeeded)

`PATCH /api/now/table/incident/{sys_id}`

```json
{
  "state": "6",
  "close_code": "Solved (Permanently)",
  "close_notes": "Approved and executed in Switcheroo.",
  "work_notes": "Approved and executed in Switcheroo."
}
```

## Cancel (Networks Reject, or approve but switch write failed)

`PATCH /api/now/table/incident/{sys_id}`

```json
{
  "state": "8",
  "work_notes": "Rejected in Switcheroo.",
  "close_notes": "Rejected in Switcheroo."
}
```

**Unverified on Arup:** incident `state` 6 = Resolved and 8 = Cancelled, and `close_code` = `Solved (Permanently)`, are **out-of-box ServiceNow defaults**. Arup’s process may differ. Confirm before going live. Override with `SERVICENOW_STATE_RESOLVED`, `SERVICENOW_STATE_CANCELLED`, `SERVICENOW_CLOSE_CODE`.

## Out of scope for this POC

Bounce, port refresh, and troubleshooting **do not** create ServiceNow tickets.

## Going live (after the user exists)

1. Put the password in `.env` on the Switcheroo host only. Never commit it.
2. `SERVICENOW_ENABLED=true`
3. `SERVICENOW_DRY_RUN=false`
4. `SERVICENOW_INSTANCE=https://arup.service-now.com`
5. `SERVICENOW_USERNAME` / `SERVICENOW_PASSWORD` for the integration user
6. Restart Switcheroo. Startup **fails fast** if live mode is on and credentials are missing (it will not call SN anonymously).
7. Create one lab VLAN request and confirm an incident appears; approve/reject and confirm state updates.

Dry-run payloads (no HTTP) land in `data/servicenow-dryrun/` and `data/switcheroo.log`.
