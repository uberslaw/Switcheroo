# Security brief (for Cyber / IAM)

Switcheroo is an **internal** Client Services / Networks website. It is not designed to sit on the public internet. This note is what is already true in the code, what a shared deploy must set, and what remains a residual risk.

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
- Session cookie: `HttpOnly`, `SameSite=Lax`, 8-hour max age. `Secure` is on when `SWITCHEROO_PUBLIC_URL` is `https://` or `SWITCHEROO_COOKIE_SECURE=true`.
- CSRF: session token required on POST when `SWITCHEROO_CSRF` is on (on by default outside tests). Browsers send it from a hidden field / `X-CSRF-Token` (HTMX).
- Login lockout: 8 failures / 15 minutes per client IP (on by default outside tests). `X-Forwarded-For` is **ignored** unless `SWITCHEROO_TRUST_X_FORWARDED_FOR=true` behind a trusted proxy.
- Comparison uses `hmac.compare_digest` (no early-exit on the hash).

## Secrets: storage and transmission

| Secret | Where it lives | At rest | In transit |
| --- | --- | --- | --- |
| User login password | SQLite `users.password_hash` | **scrypt hash only** — never plaintext | HTTPS at the reverse proxy (app itself speaks HTTP on localhost) |
| Session | Signed cookie | Signed with `SWITCHEROO_SECRET_KEY` (Starlette/itsdangerous) | `Secure` cookie when HTTPS is configured |
| Switch TACACS/device password | SQLite `switches.password` | **Fernet (AES-128-CBC + HMAC) `enc:v1:`** keyed by `SWITCHEROO_DATA_KEY` (or session secret if unset) | RESTCONF HTTPS (TLS verify on by default); SSH for Netmiko fallback |
| ServiceNow password | `.env` / process env | Not in SQLite. File must be ACL’d to the service account | HTTPS Basic to the SN instance |
| Teams webhook URL | `.env` / process env | Not in SQLite. Contains a `sig` credential. Logs store **hostname only** | HTTPS POST to the allowlisted host |
| SNMP community | `.env` | Not in SQLite | UDP SNMP (optional; leave empty) |

`.env` is gitignored. Never commit it. On Windows, restrict NTFS ACLs on `.env` and `data\` to the service account.

`SWITCHEROO_DATA_KEY` should be a dedicated 32+ character random string so rotating the session key does not make device passwords unreadable.

## Shared / internal deploy checklist

1. Generate secrets (do not reuse lab values):
   - `SWITCHEROO_SECRET_KEY` — 32+ random characters
   - `SWITCHEROO_DATA_KEY` — 32+ random characters, different from the session key
2. Replace seeded `networks` / `cs` lab users under **Access**.
3. Set `SWITCHEROO_REQUIRE_HARDENED=true` so startup **fails** if the lab key, missing data key, or cleartext public URL is still in place.
4. Put TLS in front (IIS / nginx / Caddy). Set `SWITCHEROO_PUBLIC_URL=https://…` and `SWITCHEROO_COOKIE_SECURE=true`.
5. Keep `SWITCHEROO_HOST=127.0.0.1` and let the proxy connect locally, **or** bind `0.0.0.0` only with host firewall allowlisting the proxy.
6. `CISCO_RESTCONF_VERIFY_TLS=true`. Dedicated TACACS user — not a personal login.
7. ServiceNow / Teams live mode only with dedicated integration credentials.

```
SWITCHEROO_REQUIRE_HARDENED=true
SWITCHEROO_SECRET_KEY=<32+ random>
SWITCHEROO_DATA_KEY=<32+ random>
SWITCHEROO_COOKIE_SECURE=true
SWITCHEROO_PUBLIC_URL=https://switcheroo.internal.example
SWITCHEROO_CSRF=true
SWITCHEROO_LOGIN_RATE_LIMIT=true
```

## Residual risk (honest)

- **No Entra SSO** — local passwords until IAM delivers it.
- **No in-process HTTPS** — the reverse proxy must terminate TLS.
- **SQLite file theft** — `enc:v1:` device passwords are useless without `SWITCHEROO_DATA_KEY`; user hashes are not reversible but can be brute-forced if the file leaks. Protect `data\` and `.env` with ACLs / disk encryption.
- **CSRF JS assist** — forms without a hidden field still work in a real browser because `app.js` injects the token; TestClient does not run JS, so automated tests keep CSRF off unless a test turns it on.
- **Lab first-run** remains convenient on loopback with documented lab passwords. That mode is **not** a shared deploy.
