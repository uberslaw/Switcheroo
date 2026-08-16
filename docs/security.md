# Security brief (for Cyber / IAM)

Switcheroo is an **internal** Client Services / Networks website. It is not designed to sit on the public internet. This note is what is already true in the code, what a shared deploy must set, and what remains a residual risk.

Cursor agents follow `.cursor/skills/hardening/SKILL.md` so this brief and the app stay aligned.

## How the app is kept from being “open”

| Control | Default | What it does |
| --- | --- | --- |
| Bind address | `127.0.0.1:8080` | First run is loopback only. `0.0.0.0` is refused unless `SWITCHEROO_SECRET_KEY` is no longer the lab default. |
| No public API docs | `docs_url=None` | FastAPI `/docs` and `/redoc` are off. |
| Auth on pages | Session cookie | Unauthenticated users are sent to `/login`. CS cannot approve writes. Networks-only routes return 403. |
| CS data scope | Permission table | CS only sees switches they are granted. |
| Writes | Approval queue | VLAN/bounce/no-shutdown do not hit a switch until Networks approves (unless an auto-approve policy is on). |
| Teams / ServiceNow | Dry-run default | No HTTP to Microsoft or ServiceNow until explicitly enabled with credentials. |
| Teams webhook SSRF | Host allowlist | Live webhook URLs must be `https` on Teams / Power Automate hosts. |
| Open redirect | Path allowlist | Login `next` and approve `next` must be same-origin relative paths. |
| Host header | Lab: off (`*`) | Shared deploy: `SWITCHEROO_ALLOWED_HOSTS` must list the real hostname(s). |
| OpenAPI / health | `/health` only | Health does not return secrets. |

Do **not** put Switcheroo on the public internet. Bind `0.0.0.0` only on a firewalled internal host, behind a reverse proxy that terminates TLS.

## Languages and libraries

- Runtime: **Python 3.12 or newer** (startup fails on older interpreters).
- Dependencies are **pinned** in `requirements.txt` (FastAPI, SQLAlchemy, httpx, cryptography, …).
- Install into a venv: `python -m pip install -r requirements.txt`.
- For a shared host, re-check pins with `python -m pip install pip-audit` then `pip-audit -r requirements.txt` (or the org’s SCA tool) before go-live and on a regular cadence.

## Authentication

- Local username/password (v1). **Not Entra ID / SSO.**
- Passwords are stored as **scrypt** hashes (`hashlib.scrypt`, unique salt). Legacy `pbkdf2$` hashes still verify so existing lab DBs keep working.
- Unknown usernames still run a dummy scrypt verify so timing does not confirm whether the account exists. Login error text is generic.
- Session cookie: `HttpOnly`, `SameSite=Lax`, 8-hour **absolute** max age (no idle timeout). `Secure` is on when `SWITCHEROO_PUBLIC_URL` is `https://` or `SWITCHEROO_COOKIE_SECURE=true`. The session is **signed, not encrypted** — nothing secret is stored in it.
- The session is cleared and rebuilt on successful login (session fixation). Logout clears it.
- CSRF: session token required on POST when `SWITCHEROO_CSRF` is on (on by default outside tests). Browsers send it from a hidden field / `X-CSRF-Token` (HTMX).
- Login lockout: 8 failures / 15 minutes per client IP (on by default outside tests). In-process memory only (resets on restart). `X-Forwarded-For` is **ignored** unless `SWITCHEROO_TRUST_X_FORWARDED_FOR=true` behind a trusted proxy that overwrites that header.
- Comparison uses `hmac.compare_digest` (no early-exit on the hash).
- Admin-created users need a password of **10+ characters**. Lab seed accounts stay short on purpose and are **not created** when `SWITCHEROO_REQUIRE_HARDENED=true`.
- Hardened first-user: `SWITCHEROO_BOOTSTRAP_USERNAME` + `SWITCHEROO_BOOTSTRAP_PASSWORD` (12+). Startup fails if hardened mode would leave the database with nobody who can sign in. Enabling hardened on an already-seeded lab DB does **not** delete `networks` / `cs` — change those passwords.

## Secrets: storage and transmission

| Secret | Where it lives | At rest | In transit |
| --- | --- | --- | --- |
| User login password | SQLite `users.password_hash` | **scrypt hash only** — never plaintext | HTTPS at the reverse proxy (app itself speaks HTTP on localhost) |
| Session | Signed cookie | Signed with `SWITCHEROO_SECRET_KEY` (Starlette/itsdangerous) | `Secure` cookie when HTTPS is configured |
| Switch TACACS/device password | SQLite `switches.password` | **Fernet (AES-128-CBC + HMAC) `enc:v1:`** keyed by `SWITCHEROO_DATA_KEY` (or session secret if unset) | RESTCONF HTTPS (TLS verify on by default); SSH for Netmiko fallback |
| ServiceNow password | `.env` / process env | Not in SQLite. File must be ACL’d to the service account | HTTPS Basic to the SN instance |
| Teams webhook URL | `.env` / process env | Not in SQLite. Contains a `sig` credential. Logs store **hostname only** | HTTPS POST to the allowlisted host |
| SNMP community | `.env` | Not in SQLite | UDP SNMP (optional; leave empty) |

`.env` is gitignored. Never commit it. On Windows, restrict NTFS ACLs on `.env` and `data\` to the service account. On Unix the app sets `data/` to `0700` and the SQLite file, log, and `audit.log` to `0600` when it can.

`SWITCHEROO_DATA_KEY` should be a dedicated 32+ character random string so rotating the session key does not make device passwords unreadable.

## HTTP headers

Authenticated pages send `Cache-Control: no-store`. Responses also include `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a restrictive CSP, `Referrer-Policy: same-origin`, and `Permissions-Policy` disabling camera/mic/geo. `Strict-Transport-Security` is set only when Secure cookies are on (HTTPS).

## Audit log

`data/audit.log` (JSON lines, `0600`) records login success/failure, logout, user creation, VLAN/bounce request create, approve, reject, and acknowledgement. It never stores passwords, tokens, or webhook URLs.

## Shared / internal deploy checklist

1. Generate secrets (do not reuse lab values):
   - `SWITCHEROO_SECRET_KEY` — 32+ random characters
   - `SWITCHEROO_DATA_KEY` — 32+ random characters, different from the session key
2. Set `SWITCHEROO_BOOTSTRAP_PASSWORD` (12+) for the first Networks admin on an **empty** database, then change it under **Access**. Do not keep README lab passwords. If the DB was already seeded in lab mode, change or delete `networks` / `cs`.
3. Set `SWITCHEROO_REQUIRE_HARDENED=true` so startup **fails** if the lab key, missing data key, cleartext public URL, Secure cookies, or allowed hosts are still wrong.
4. Put TLS in front (IIS / nginx / Caddy). Set `SWITCHEROO_PUBLIC_URL=https://…` and `SWITCHEROO_COOKIE_SECURE=true`.
5. Set `SWITCHEROO_ALLOWED_HOSTS` to that hostname (no `*`).
6. Keep `SWITCHEROO_HOST=127.0.0.1` and let the proxy connect locally, **or** bind `0.0.0.0` only with host firewall allowlisting the proxy.
7. `CISCO_RESTCONF_VERIFY_TLS=true`. Dedicated TACACS user — not a personal login.
8. ServiceNow / Teams live mode only with dedicated integration credentials.

```
SWITCHEROO_REQUIRE_HARDENED=true
SWITCHEROO_SECRET_KEY=<32+ random>
SWITCHEROO_DATA_KEY=<32+ random>
SWITCHEROO_COOKIE_SECURE=true
SWITCHEROO_PUBLIC_URL=https://switcheroo.internal.example
SWITCHEROO_ALLOWED_HOSTS=switcheroo.internal.example
SWITCHEROO_BOOTSTRAP_USERNAME=networks
SWITCHEROO_BOOTSTRAP_PASSWORD=<12+ random, then change in Access>
SWITCHEROO_CSRF=true
SWITCHEROO_LOGIN_RATE_LIMIT=true
SWITCHEROO_TRUST_X_FORWARDED_FOR=false
```

## Residual risk (honest)

- **No Entra SSO** — local passwords until IAM delivers it.
- **No in-process HTTPS** — the reverse proxy must terminate TLS.
- **Starlette session is signed, not encrypted** — do not put secrets in the session.
- **No idle session timeout** — cookies live until the 8-hour max-age or logout.
- **Login lockout is in-process memory** — not shared across workers; resets on restart.
- **SQLite file theft** — `enc:v1:` device passwords are useless without `SWITCHEROO_DATA_KEY`; user hashes are not reversible but can be brute-forced if the file leaks. Protect `data\` and `.env` with ACLs / disk encryption.
- **Networks can set any management IP** — that role is trusted to talk to switches (SSRF-shaped by design).
- **CSRF JS assist** — forms without a hidden field still work in a real browser because `app.js` injects the token; TestClient does not run JS, so automated tests keep CSRF off unless a test turns it on.
- **Lab first-run** remains convenient on loopback with documented lab passwords. That mode is **not** a shared deploy.
- **Hardened mode does not scrub an existing lab database** — change published passwords if the file was seeded before `REQUIRE_HARDENED` was turned on.
