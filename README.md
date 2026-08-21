# Switcheroo

Project location: **`C:\Switcheroo`** (moved from `C:\Users\christopher.owen\Projects\switcheroo`).

Internal website for Client Services and Networks to see Cisco Catalyst 9300 access-switch port status and request small changes (VLAN, bounce, no-shutdown). Writes run only after Networks approval, unless an auto-approve policy matches.

This is a **lab-first v1**. Default driver is an in-process simulator so the site works on a fresh machine with no switches.

## Assumptions (read before first run)

| Topic | What this repo assumes |
| --- | --- |
| Runtime | Python **3.12 or newer** on PATH (`python`). Verified on this host with 3.14. Not bundled. |
| OS | Windows PowerShell 5.1+ (scripts below). Linux/macOS work with the same `python -m` flow (no WinSW service). |
| Privileges | **Install / Start / Stop / Restart of the Windows service** needs administrator. Preferred ops path: run **Master Launch Control as administrator once**, leave it open, then **Open Launch Control** from the card (this window inherits that token; Install runs in-process with no second UAC). Launch Control can **watch** status, PID, logs, and `/health` without admin. Admin does **not** unlock a pywin32 DLL held by a running Python service — Stop first. |
| Network | Safe first-run default is **127.0.0.1:8080** (this machine only). For other PCs on the internal LAN set `SWITCHEROO_HOST=0.0.0.0` and allow inbound TCP 8080. No campus switches, RESTCONF, SSH, or ServiceNow are required. |
| Data | SQLite + logs under `data\` (created at startup). The process must be able to write that folder. |
| Auth | Local username/password. **Not Entra SSO.** Seeded lab passwords are public in this README. Each VLAN ticket also records the **Windows account of the process running Switcheroo** (`USERDOMAIN\USERNAME`). Fine for a local POC on a CS/Networks PC; a shared server would need SSO, or the field would be the service account. |
| Hardware | `SWITCHEROO_DRIVER=simulator` by default. Real 9300s need RESTCONF (preferred), SSH/Netmiko fallback, optional SNMP, and a **dedicated TACACS or local user** — never a personal login. |

Lab-only defaults that must change before any shared/internal deploy:

- Bind address `127.0.0.1` (set `SWITCHEROO_HOST=0.0.0.0` only on a firewalled internal host)
- `SWITCHEROO_SECRET_KEY=change-me-lab-only-not-for-production`
- Users `networks` / `networks` and `cs` / `cs`
- Simulated mgmt IPs `192.0.2.10` / `192.0.2.11` (lab buildings) and `192.0.2.21`–`192.0.2.62` (Brisbane template). RFC 5737 TEST-NET-1, not live devices.

## Windows first run (service + Launch Control)

The supported operator path is a **Windows service** so the site comes back after reboot. **Launch Control** is an out-of-band monitor — not the website.

### 1. Install the service (administrator, once)

Python **3.12+** must be on PATH the first time (the installer creates `.venv` and copies `.env` if missing). WinSW (MIT) is downloaded into `scripts\winsw\` or you can drop `WinSW.NET461.exe` there for an offline box. No Visual Studio.

```powershell
# Elevated PowerShell
cd C:\Switcheroo
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-service.ps1
```

The script fail-fast checks Python, venv, `.env`, and a writable `data\`, then installs service **`Switcheroo`** (`C:\Switcheroo\.venv\Scripts\python.exe -m app`, working directory `C:\Switcheroo`), sets **Automatic Delayed Start** and **restart on failure**, starts it, and verifies:

- `Get-Service Switcheroo` is **Running**
- `GET http://127.0.0.1:8080/health` returns `ok`

Logs: `data\install-service.log`, `data\switcheroo.log`, plus WinSW `data\Switcheroo.out.log` / `.err.log` / `.wrapper.log`.

Uninstall (also elevated): `.\scripts\uninstall-service.ps1`

### 2. Launch Control (monitor)

Double-click **`scripts\Switcheroo-LaunchControl.cmd`** (or run `scripts\New-SwitcherooShortcuts.cmd` once for a desktop shortcut).

The window must show:

- **Status** from the Windows service (Running / Stopped / Starting / Stopped (failed)) or from an attached `python -m app` if the service is not installed
- **PID** of the listening python process (never `-` while Running)
- **Live console** — last 200 lines of `data\switcheroo.log` and follow (the black pane is a log tail; empty is a bug)
- **Health** — `GET /health` ok/fail and latency
- Bind URL and python path in the header

Start / Stop / Restart talk to the **service** when it is installed. Without elevation those buttons refuse with a message (not a silent no-op); status, PID, logs, and health still update.

If the service is not installed yet, Start will launch `python -m app` for this session only (dies at logoff / reboot). Use **Install Windows service** in the UI. If Launch Control is already admin (opened from an elevated Master Launch Control), install runs in this token with no extra UAC. Otherwise Windows will prompt. **Stop** the service before reinstall if Python still holds this venv's pywin32 DLL.

### 3. Optional: raw console (no service)

`.\run.ps1` or:

```powershell
cd C:\Switcheroo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m app
```

Open http://127.0.0.1:8080

| Username | Password | Role |
| --- | --- | --- |
| `networks` | `networks` | Inventory, port purposes, CS permissions, approval queue |
| `cs` | `cs` | All switches while `SWITCHEROO_OPEN_ACCESS=true` (lab default); otherwise only granted switches |

These are **lab defaults**. Create real users under Access before sharing the site.

### Remote access from another computer (internal LAN only)

This is **not** a public internet app. Bind all interfaces only on a firewalled internal host. Do not port-forward 8080 and do not disable Windows Firewall.

1. In `.env` set `SWITCHEROO_HOST=0.0.0.0` (keep `SWITCHEROO_PORT=8080` unless you already changed it). Restart the **Switcheroo** service (or `python -m app`). Confirm listen with `Get-NetTCPConnection -LocalPort 8080` — you want `0.0.0.0:8080` or `[::]:8080` **Listen**, not `127.0.0.1:8080`.
2. Allow **inbound TCP 8080** on the Windows Firewall profile the NIC actually uses (**Domain** vs **Private**). A rule on the wrong profile looks like it was added but still blocks.
3. Clients open `http://<this-machine-LAN-ip>:8080` (not `127.0.0.1`). Example: `http://192.168.1.10:8080`.

| Symptom | Meaning |
| --- | --- |
| Connection **refused** | Nothing is listening on the LAN NIC (still bound to loopback, or the process is down). |
| Connection **timeout** | Packet is dropped — firewall profile, routing, or a filter in between. |

Logs: `data\switcheroo.log`, `data\diagnostics.log` (after **Diagnostics ON** in Launch Control)  
Database: `data\switcheroo.db`

## Polling load (Catalyst 9300)

The poller is designed **not** to tax the boxes:

- **Every 60s** (configurable 60–180): **targeted interface status only** (`ifOperStatus` / admin status for known ports). No full IF-MIB / VLAN / MAC table walk on this timer.
- **Daily** (default 02:00 UTC): VLAN number + name, connected MAC, IP if any, ISE/auth session. Also available as **on-demand refresh**.
- **On-demand refresh**: VLAN/MAC/IP/ISE + status. Shared **60 second cooldown per port** (not per user) so two CS people cannot stampede the same interface.
- **Troubleshooting mode**: one port, every **10 seconds for 5 minutes**, then auto-stop. One active session per user. Does not multiply into overlapping extra sessions for that user.

A failed poll is recorded on the switch/port and **does not crash** the website.

### Faceplate, LEDs, and connected uptime

The switch page is a **Catalyst 9300-style faceplate** (48 RJ45, odd-over-even, four groups of 12, SFP/NM cages on the right). Click a port to fill the right-hand detail pane. Brisbane **core** members use a distinct **Catalyst 9500-looking** chassis (wider QSFP uplink bay) still with 48 copper ports.

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

Workbook columns: switch, port, purpose, label, status, admin, VLAN, VLAN name, MAC, IP, ISE, connected uptime, last status poll, last detail poll. CS only receives switches they are permitted to see unless `SWITCHEROO_OPEN_ACCESS=true` (lab default: everyone sees every switch). Requires `openpyxl` (pinned in `requirements.txt`).

## Brisbane office template

Seed creates a 20-switch Brisbane campus (plus the two original lab boxes). `Switch.location` is **Brisbane**, so CS office auto-approve and the Requests office filter use that name. Networks can rename stacks/members on **Inventory**. Seed **upserts** by switch name: it still runs when `data\switcheroo.db` already exists, fills missing Brisbane rows and layout fields, and does not duplicate. The CS grant table is still populated; while **`SWITCHEROO_OPEN_ACCESS=true`** (default in `.env.example`) every signed-in user sees every switch — set it `false` later to enforce grants.

After seed, **restart the Switcheroo Windows service** so the running process hydrates the simulator (`Restart-Service Switcheroo` from an elevated prompt). Seed also runs at process start. If the service cannot be restarted from this session, seed the live file with `python -c` against `data\switcheroo.db` then restart so `/` is not empty.

| What to open | URL |
| --- | --- |
| Home (Brisbane racks + lab cards) | http://127.0.0.1:8080/ |
| Brisbane office layout | http://127.0.0.1:8080/offices/brisbane |
| **Rack Design** (Albert St elevations) | http://127.0.0.1:8080/racks |
| Example floor member faceplate | home → **BNE-L27-FS-01** (or Inventory) |
| Example aux (top of aux rack is #3) | **BNE-L27-AUX-03** |
| Example 9500 core | **BNE-L27-CORE-01** |
| Auto-approve office **Brisbane** | http://127.0.0.1:8080/admin/policies |
| Rack Design permissions (Networks) | http://127.0.0.1:8080/admin/rack-permissions |

**How it looks**

- **Home / Brisbane page:** not a flat list of 20 cards. **Floor stacks** are three vertical racks (L27 = 7, L26 = 5, L21 = 3), member **#1 at the top**.
- **Level 27 Main Comms Room:** three columns — L27 floor stack (those 7 live in the MCR), aux stack, core 9500 stack.
- **Aux physical order** (top → bottom): **#3, #1, #2** (`BNE-L27-AUX-03`, `BNE-L27-AUX-01`, `BNE-L27-AUX-02`). `rack_order` stores that, so the UI does not sort 1-2-3.
- **L26 / L21** floor stacks are their own IDFs (not inside the MCR). Click a mini chassis for the existing full faceplate.

**Rack Design** (separate from the switch-stack “racks” on the Brisbane page): seed imports `app/seed_data/Brisbane_Albert_St_Rack_Layout.xlsx` into editable elevations (MCR L27 four racks, FDR L26, FDR L21). RU numbering matches the workbook — **high RU at the top, RU 1 at the bottom**. Front/back faces, place/move/edit gear from a category catalog.

Rack capabilities are per user (Networks has all of them):

| Capability | Lets you |
| --- | --- |
| `rack_view` | See sites and elevations |
| `rack_edit_layout` | Place, move, rename, and remove gear |
| `rack_manage_racks` | Create sites and racks, set the **RU limit**, rename or delete a rack |
| `rack_manage_catalog` | Add catalog categories and item types |
| `rack_manage_permissions` | Grant the above |

CS is seeded with **view + edit layout**. Networks toggles the rest under **Access → Rack permissions**. Shrinking a rack's RU limit below gear that is already placed is refused and names the blocking RU. Cable tracing and a fuller device DB are listed in `planned-improvements.md`.

**Naming (Networks can rename)**

| Role | Names | Room |
| --- | --- | --- |
| L27 floor stack | `BNE-L27-FS-01` … `BNE-L27-FS-07` | Level 27 Main Comms Room |
| L26 floor stack | `BNE-L26-FS-01` … `BNE-L26-FS-05` | Level 26 IDF |
| L21 floor stack | `BNE-L21-FS-01` … `BNE-L21-FS-03` | Level 21 IDF |
| L27 aux | `BNE-L27-AUX-03`, `BNE-L27-AUX-01`, `BNE-L27-AUX-02` | Level 27 Main Comms Room |
| L27 core 9500 | `BNE-L27-CORE-01`, `BNE-L27-CORE-02` | Level 27 Main Comms Room |

## Drivers

1. **Simulator (default)** — seeded 48-port fake switches: two lab boxes (`CS-BLD-A-AS01`, `CS-BLD-B-AS01`) plus the **Brisbane office template** (20 switches, names below). Mixed purposes, some down, some shutdown, MAC/IP/ISE, named VLANs, and lab connected-uptime stamps. Seed is idempotent. Driver stays `simulator` unless you override a row.
2. **CiscoIOSXE** — RESTCONF structured reads/writes; Netmiko SSH fallback for bounce / shutdown / VLAN; SNMP optional for lightweight ifOperStatus. **No connection is opened** unless the switch row has management IP + username + password. Missing secrets stay on the simulator.

`SWITCHEROO_DRIVER=cisco_iosxe` is global; a per-switch override exists on the inventory form.

Real-box checklist (not done by this app):

- RESTCONF enabled on the 9300, reachable on 443 from this host
- SSH (Netmiko) for bounce/shutdown if RESTCONF write is denied
- Optional SNMP community only if you want ifOperStatus via SNMP
- Dedicated TACACS/local user with the least privilege Networks will accept

## ServiceNow (VLAN-change POC)

Switcheroo is where people click. ServiceNow is the ticket log. **Only VLAN change requests** create SN records (not bounce, refresh, or troubleshoot).

VLAN changes require a **reason** (right-hand port pane textarea). Empty/whitespace is rejected — no ChangeRequest and no ServiceNow ticket, including when auto-approve would otherwise fire.

Each ticket stores the Switcheroo username, the **Windows account** of the process (`USERDOMAIN\USERNAME`, fallback `getpass.getuser()`), and the reason. Those go into the SN description and work notes. The Windows account is the identity of the **process running Switcheroo** — fine for a local POC on someone’s PC. Later, a shared server would need SSO, or this field would show the service account.

Arup workflow is catalog-style. The number people quote is the **RITM**, then the parent **REQ**. The Networks approval queue, `/requests`, and the pending VLAN pane show RITM first.

| Mode | Env | Behaviour |
| --- | --- | --- |
| Dry-run (default) | `SERVICENOW_ENABLED=false` and/or `SERVICENOW_DRY_RUN=true` | Local request + visible `RITM-DRY-RUN` / `REQ-DRY-RUN`. Payload written to `data/servicenow-dryrun/`. **No HTTP** to arup.service-now.com. |
| Live | `SERVICENOW_ENABLED=true`, `SERVICENOW_DRY_RUN=false`, username + password set | Prefer Table API `sc_req_item` + parent `sc_request`. If `SERVICENOW_CATALOG_ITEM_SYS_ID` is set, may `order_now` that catalog item. `SERVICENOW_TABLE` (default `incident`) is fallback only. |

If live mode is on and credentials are missing, startup **fails fast** and does not call ServiceNow anonymously. If the catalog item sys_id is unset, the SN team must provide the VLAN-change catalog item before `order_now` can be used.

Give this to ServiceNow / IAM: **[docs/servicenow-poc.md](docs/servicenow-poc.md)** (integration user, RITM/REQ sample JSON, poll query, resolve/cancel fields).

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
scripts/       Launch Control, install-service.ps1 / uninstall-service.ps1, WinSW wrapper
app/            FastAPI app (uvicorn app.main:app)
app/drivers/    SwitchDriver: simulator + cisco_iosxe + ServiceNow Table API + Teams webhook (dry-run default)
docs/           IAM / ServiceNow POC brief, Teams webhook setup, security brief for Cyber
app/services/   polling, cooldown, approvals
app/templates/  Jinja2 + HTMX
tests/
data/           sqlite + switcheroo.log (created at runtime, not committed)
```

Give this to Cyber: **[docs/security.md](docs/security.md)** (bind address, hashing, encrypted device secrets, CSRF, cookies, residual risk). In the running app: **Help → Security checklist**.
Cursor agents: **[`.cursor/skills/hardening/SKILL.md`](.cursor/skills/hardening/SKILL.md)** (checklist of controls people usually miss).

## Gaps (v1)

- Installing the Windows service cannot be proven in un-elevated CI. On a real box run the elevated `install-service.ps1` command above; expected: service Running and `/health` ok.
- Entra ID / SSO is not implemented (local users only).
- ServiceNow live Table API is implemented but **off** until an integration user exists. Arup incident `state` / `close_code` values are unverified.
- Real 9300 YANG paths may need site-specific adjustment once RESTCONF is pointed at a lab switch.
- No HTTPS terminator in-process; put one in front if you bind beyond loopback. Set `SWITCHEROO_REQUIRE_HARDENED=true` before a shared deploy (that also blocks well-known lab users on an empty database).
