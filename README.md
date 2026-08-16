# Switcheroo

Project location: **`C:\Switcheroo`** (moved from `C:\Users\christopher.owen\Projects\switcheroo`).

Internal website for Client Services and Networks to see Cisco Catalyst 9300 access-switch port status and request small changes (VLAN, bounce, no-shutdown). Writes run only after Networks approval, unless an auto-approve policy matches.

This is a **lab-first v1**. Default driver is an in-process simulator so the site works on a fresh machine with no switches.

## Assumptions (read before first run)

| Topic | What this repo assumes |
| --- | --- |
| Runtime | Python **3.12 or newer** on PATH (`python`). Verified on this host with 3.14. Not bundled. |
| OS | Windows PowerShell (commands below). Linux/macOS work with the same `python -m` flow. |
| Network | First run binds **127.0.0.1:8080** only. No campus switches, RESTCONF, SSH, or ServiceNow are required. |
| Data | SQLite + logs under `data\` (created at startup). The process must be able to write that folder. |
| Auth | Local username/password. **Not Entra SSO.** Seeded lab passwords are public in this README. |
| Hardware | `SWITCHEROO_DRIVER=simulator` by default. Real 9300s need RESTCONF (preferred), SSH/Netmiko fallback, optional SNMP, and a **dedicated TACACS or local user** — never a personal login. |

Lab-only defaults that must change before any shared/internal deploy:

- Bind address `127.0.0.1` (set `SWITCHEROO_HOST=0.0.0.0` only on a firewalled internal host)
- `SWITCHEROO_SECRET_KEY=change-me-lab-only-not-for-production`
- Users `networks` / `networks` and `cs` / `cs`
- Simulated mgmt IPs `192.0.2.10` and `192.0.2.11` (RFC 5737 TEST-NET-1, not live devices)

## Windows first run

```powershell
cd C:\Switcheroo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m app
```

Or: `.\run.ps1`

Open http://127.0.0.1:8080

| Username | Password | Role |
| --- | --- | --- |
| `networks` | `networks` | Inventory, port purposes, CS permissions, approval queue |
| `cs` | `cs` | Permitted switches, request VLAN/bounce/bring-online, on-demand refresh, troubleshooting |

These are **lab defaults**. Create real users under Access before sharing the site.

To listen on the LAN (internal only):

```powershell
# in .env
SWITCHEROO_HOST=0.0.0.0
```

Then restrict with Windows Firewall / reverse proxy. Do not put Switcheroo on the public internet.

Logs: `data\switcheroo.log`  
Database: `data\switcheroo.db`

## Polling load (Catalyst 9300)

The poller is designed **not** to tax the boxes:

- **Every 60s** (configurable 60–180): **targeted interface status only** (`ifOperStatus` / admin status for known ports). No full IF-MIB / VLAN / MAC table walk on this timer.
- **Daily** (default 02:00 UTC): VLAN number + name, connected MAC, IP if any, ISE/auth session. Also available as **on-demand refresh**.
- **On-demand refresh**: VLAN/MAC/IP/ISE + status. Shared **60 second cooldown per port** (not per user) so two CS people cannot stampede the same interface.
- **Troubleshooting mode**: one port, every **10 seconds for 5 minutes**, then auto-stop. One active session per user. Does not multiply into overlapping extra sessions for that user.

A failed poll is recorded on the switch/port and **does not crash** the website.

### Faceplate, LEDs, and connected uptime

The switch page is a **Catalyst 9300-style faceplate** (48 RJ45, odd-over-even, four groups of 12, SFP/NM cages on the right). Click a port to fill the right-hand detail pane.

Port LED legend:

| LED | Meaning |
| --- | --- |
| Light green | Link up / active (admin up, forwarding) |
| Dark green | Not connected (admin up, no link) |
| Red | Problem or administratively shutdown |

**Connected uptime:** each status poll tracks `link_up_since`. Down→up (or the first time we see a port already up) stamps *that poll time* — Switcheroo does not invent a longer history. Up→down or shutdown clears it. The UI shows `Connected for 3h 12m` or `Not connected`. The simulator seeds already-up lab ports with a past stamp so the first-run UI is not all zeros.

### Export XLSX

- Per switch: `/switches/{id}/export.xlsx` (also the **Export XLSX** button on the faceplate page).
- All switches the user can see: `/export.xlsx` (dashboard and Networks inventory).

Workbook columns: switch, port, purpose, label, status, admin, VLAN, VLAN name, MAC, IP, ISE, connected uptime, last status poll, last detail poll. CS only receives switches they are permitted to see. Requires `openpyxl` (pinned in `requirements.txt`).

## Drivers

1. **Simulator (default)** — two seeded 48-port fake 9300s (`CS-BLD-A-AS01`, `CS-BLD-B-AS01`) with mixed purposes, some down, some shutdown, MAC/IP/ISE, named VLANs, and lab connected-uptime stamps. Seed is idempotent.
2. **CiscoIOSXE** — RESTCONF structured reads/writes; Netmiko SSH fallback for bounce / shutdown / VLAN; SNMP optional for lightweight ifOperStatus. **No connection is opened** unless the switch row has management IP + username + password. Missing secrets stay on the simulator.

`SWITCHEROO_DRIVER=cisco_iosxe` is global; a per-switch override exists on the inventory form.

Real-box checklist (not done by this app):

- RESTCONF enabled on the 9300, reachable on 443 from this host
- SSH (Netmiko) for bounce/shutdown if RESTCONF write is denied
- Optional SNMP community only if you want ifOperStatus via SNMP
- Dedicated TACACS/local user with the least privilege Networks will accept

## ServiceNow (VLAN-change POC)

Switcheroo is where people click. ServiceNow is the ticket log. **Only VLAN change requests** create SN records (not bounce, refresh, or troubleshoot).

| Mode | Env | Behaviour |
| --- | --- | --- |
| Dry-run (default) | `SERVICENOW_ENABLED=false` and/or `SERVICENOW_DRY_RUN=true` | Local request + `SN-DRY-RUN` ticket. Payload written to `data/servicenow-dryrun/`. **No HTTP** to arup.service-now.com. |
| Live | `SERVICENOW_ENABLED=true`, `SERVICENOW_DRY_RUN=false`, username + password set | `POST/GET/PATCH` Table API on `SERVICENOW_TABLE` (default `incident`). |

If live mode is on and credentials are missing, startup **fails fast** and does not call ServiceNow anonymously.

Give this to ServiceNow / IAM: **[docs/servicenow-poc.md](docs/servicenow-poc.md)** (integration user, sample JSON, poll query, resolve/cancel fields).

Review all requests (CS sees permitted offices/switches only): **http://127.0.0.1:8080/requests**

Networks can **Approve & run** / **Reject** pending rows on that page (or on **http://127.0.0.1:8080/admin/approvals**).

## Teams (VLAN-change channel alert)

A Teams webhook is the pager so Networks do not have to keep the console open. **Only pending VLAN change requests** post a card (not bounce, refresh, troubleshoot, or auto-approved writes).

| Mode | Env | Behaviour |
| --- | --- | --- |
| Dry-run (default) | `TEAMS_ENABLED=false` and/or `TEAMS_DRY_RUN=true` | Local request + payload written to `data/teams-dryrun/`. **No HTTP** to Microsoft. |
| Live | `TEAMS_ENABLED=true`, `TEAMS_DRY_RUN=false`, `TEAMS_WEBHOOK_URL` + `SWITCHEROO_PUBLIC_URL` set | `POST` Adaptive Card (Workflows) or MessageCard (classic Incoming Webhook) to the channel. |

The card says a VLAN change request has been generated, includes switch/port/VLAN facts, and has two links:

- **I'm on it** → `{SWITCHEROO_PUBLIC_URL}/requests/{id}/ack` (claim the work so nobody doubles up)
- **Open request page** → `{SWITCHEROO_PUBLIC_URL}/requests?status=pending#request-{id}` (accept or reject)

Incoming webhooks cannot edit the original card in place. When someone acknowledges, Switcheroo posts a **follow-up** card: *VLAN request #N acknowledged — {name} is handling this — no need to pick it up.* Release posts that the request is available again.

If live mode is on and the webhook or public URL is missing (or the webhook host is not Teams/Power Automate), startup **fails fast**. A Teams POST failure is logged and **does not** drop the local request.

Give this to whoever owns the Networks channel: **[docs/teams-webhook.md](docs/teams-webhook.md)**.

## Auto-approve (Networks Policies)

Default is **all off** — requests stay on the Networks queue. If **any** matching rule is on, the request runs immediately (VLAN still creates the ServiceNow ticket first, then resolve/close like a human Approve).

| Scope | How to turn on | Matches |
| --- | --- | --- |
| Everywhere | **Policies** → Everywhere → On | All CS VLAN / bounce / bring-online |
| Office | **Policies** → that `Switch.location` → On | Requests for switches in that office |
| Requestor | **Policies** → that CS user → On | That person’s requests (all offices they can see) |

Most-open wins: global, then office, then requestor. The Requests page shows **Auto-approved** plus the rule (`Everywhere` / `Office: …` / `Requestor: …`). CS cannot change policies. Page: **http://127.0.0.1:8080/admin/policies**

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest --timeout=30 --timeout-method=thread
```

## Project layout

```
app/            FastAPI app (uvicorn app.main:app)
app/drivers/    SwitchDriver: simulator + cisco_iosxe + ServiceNow Table API + Teams webhook (dry-run default)
docs/           IAM / ServiceNow POC brief, Teams webhook setup, security brief for Cyber
app/services/   polling, cooldown, approvals
app/templates/  Jinja2 + HTMX
tests/
data/           sqlite + switcheroo.log (created at runtime, not committed)
```

Give this to Cyber: **[docs/security.md](docs/security.md)** (bind address, hashing, encrypted device secrets, CSRF, cookies, residual risk).
Cursor agents: **[`.cursor/skills/hardening/SKILL.md`](.cursor/skills/hardening/SKILL.md)** (checklist of controls people usually miss).

## Gaps (v1)

- Entra ID / SSO is not implemented (local users only).
- ServiceNow live Table API is implemented but **off** until an integration user exists. Arup incident `state` / `close_code` values are unverified.
- Real 9300 YANG paths may need site-specific adjustment once RESTCONF is pointed at a lab switch.
- No HTTPS terminator in-process; put one in front if you bind beyond loopback. Set `SWITCHEROO_REQUIRE_HARDENED=true` before a shared deploy (that also blocks well-known lab users on an empty database).
