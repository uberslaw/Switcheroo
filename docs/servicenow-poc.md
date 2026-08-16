# ServiceNow integration brief (give this to ServiceNow / IAM)

Switcheroo is an internal Client Services / Networks website. People request **VLAN changes** there. ServiceNow is the **ticket log**, not the place staff click to change a port.

This is a **POC**. Defaults stay dry-run. No live call is made to `https://arup.service-now.com` until `SERVICENOW_ENABLED=true`, `SERVICENOW_DRY_RUN=false`, and a dedicated integration user is set.

Arup’s workflow is **catalog-style**. The number CS and Networks will quote is the **RITM** (`sc_req_item`). The parent **REQ** (`sc_request`) is shown next to it. A plain incident number is fallback only.

## What to request

| Item | Value |
| --- | --- |
| Instance | `https://arup.service-now.com` |
| Account type | **Dedicated integration user** (not a personal login, not a CS/Networks named user) |
| Protocol | HTTPS Table API, **HTTP Basic** (username + password). Optional Catalog API `order_now` if a catalog item sys_id is provided. |
| Tables (required) | **`sc_req_item`** and **`sc_request`**: **create, read, write** |
| Table (fallback) | `incident` (configurable via `SERVICENOW_TABLE`) if catalog create is not ready |
| Catalog item | VLAN-change item sys_id (`SERVICENOW_CATALOG_ITEM_SYS_ID`). **If unset, the SN team must provide this** before Switcheroo can call `/api/sn_sc/servicecatalog/items/{sys_id}/order_now`. Table API create of `sc_req_item` still works without it. |
| Source | Internal server process (Switcheroo). Not a browser user. Provide the app host’s **egress IP** so SN/IAM can allowlist if required. |
| Roles (typical — confirm with SN) | `rest_service` plus Table API ACL on `sc_req_item` and `sc_request` (and `incident` if fallback stays). Catalog API needs the catalog item + `sn_sc` order permission if `order_now` is used. |

Do **not** grant the integration user a human desktop role unless SN requires it for Table API.

## Create (CS VLAN request)

Preferred: `POST /api/now/table/sc_req_item`

Then read the parent `request` reference (`GET /api/now/table/sc_request/{sys_id}`) for the **REQ** number.

```json
{
  "short_description": "[Switcheroo] VLAN Gi1/0/11 CS-BLD-A-AS01 10 USER -> 50 GUEST",
  "description": "Switcheroo VLAN change request (Networks must approve in Switcheroo before the port is written).\ncorrelation_id: switcheroo:vlan:42\nrequest_id: 42\nrequester: cs\nwindows_account: ARUP\\jsmith\nreason: Visitor laptop needs guest access\nswitch: CS-BLD-A-AS01\nlocation/office: Building A / IDF-1 (simulated)\nmanagement_ip: 192.0.2.10\nport: GigabitEthernet1/0/11\nfrom_vlan: 10 USER\nto_vlan: 50 GUEST",
  "correlation_id": "switcheroo:vlan:42",
  "correlation_display": "Switcheroo"
}
```

`assignment_group` is sent only if `SERVICENOW_ASSIGNMENT_GROUP` is set. Confirm the group sys_id or name with the SN team.

If `SERVICENOW_CATALOG_ITEM_SYS_ID` is set, Switcheroo may instead:

`POST /api/sn_sc/servicecatalog/items/{sys_id}/order_now`

and then read the returned **REQ** plus child **RITM**. Variable names on that catalog item must be confirmed with the SN team.

Dry-run (default) never calls the instance. It stores `RITM-DRY-RUN` and `REQ-DRY-RUN` on the local ChangeRequest and writes the same payload under `data/servicenow-dryrun/`.

## Poll (restart recovery, every 1–5 minutes)

Prefer RITMs:

`GET /api/now/table/sc_req_item?sysparm_query=correlation_idSTARTSWITHswitcheroo:vlan:^ORshort_descriptionSTARTSWITH[Switcheroo]&sysparm_fields=sys_id,number,correlation_id,short_description,state,request,request.number&sysparm_limit=200`

This is **not** per-port and does not walk campus switches. Switcheroo refreshes stored RITM and REQ numbers from the result.

## Resolve (Networks Approve + port write succeeded, including auto-approve)

`PATCH /api/now/table/sc_req_item/{sys_id}`

```json
{
  "state": "6",
  "close_code": "Solved (Permanently)",
  "close_notes": "Approved and executed in Switcheroo.\nSwitcheroo user: cs\nWindows account: ARUP\\jsmith\nChange reason: Visitor laptop needs guest access",
  "work_notes": "Approved and executed in Switcheroo.\nSwitcheroo user: cs\nWindows account: ARUP\\jsmith\nChange reason: Visitor laptop needs guest access"
}
```

Auto-approve still **creates the RITM/REQ first**, then resolves the RITM the same way as a human Approve.

## Cancel (Networks Reject, or approve but switch write failed)

`PATCH /api/now/table/sc_req_item/{sys_id}`

```json
{
  "state": "8",
  "work_notes": "Rejected in Switcheroo.\nSwitcheroo user: cs\nWindows account: ARUP\\jsmith\nChange reason: Visitor laptop needs guest access",
  "close_notes": "Rejected in Switcheroo.\nSwitcheroo user: cs\nWindows account: ARUP\\jsmith\nChange reason: Visitor laptop needs guest access"
}
```

**Unverified on Arup:** RITM/REQ `state` values and `close_code` may differ from out-of-box incident defaults (`6` Resolved, `8` Cancelled, `Solved (Permanently)`). Confirm before going live. Override with `SERVICENOW_STATE_RESOLVED`, `SERVICENOW_STATE_CANCELLED`, `SERVICENOW_CLOSE_CODE`.

## Out of scope for this POC

Bounce, port refresh, and troubleshooting **do not** create ServiceNow tickets.

## Going live (after the user exists)

1. Put the password in `.env` on the Switcheroo host only. Never commit it.
2. `SERVICENOW_ENABLED=true`
3. `SERVICENOW_DRY_RUN=false`
4. `SERVICENOW_INSTANCE=https://arup.service-now.com`
5. `SERVICENOW_USERNAME` / `SERVICENOW_PASSWORD` for the integration user
6. Ask SN for Table API on **`sc_request`** and **`sc_req_item`**, plus the VLAN-change **catalog item sys_id** (`SERVICENOW_CATALOG_ITEM_SYS_ID`)
7. Restart Switcheroo. Startup **fails fast** if live mode is on and credentials are missing (it will not call SN anonymously).
8. Create one lab VLAN request (reason required) and confirm a **RITM** and parent **REQ** appear; approve/reject and confirm the RITM state updates.

Dry-run payloads (no HTTP) land in `data/servicenow-dryrun/` and `data/switcheroo.log`.
