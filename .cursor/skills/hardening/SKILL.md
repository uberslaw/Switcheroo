---
name: hardening
description: Harden Switcheroo (and similar internal FastAPI apps) for a cyber review or shared deploy. Use when the user asks about security, cyber, IAM, secrets, CSRF, sessions, bind address, SWITCHEROO_REQUIRE_HARDENED, "is this open", production hardening, or anything they may have missed because they are not a security expert.
---

# Hardening

Apply this skill whenever Switcheroo (or a copy of it) is being reviewed by Cyber / IAM, moved off loopback, or the operator says they are parroting limited security knowledge. Do not wait for a complete threat model. Close real gaps in code, then write residual risk in `docs/security.md` in plain language.

This is **defense in depth for an internal site**, not a public SaaS. The app still must not sit on the public internet.

## When this applies

- Cyber / InfoSec / IAM review, "how open is it", "are secrets plaintext"
- Shared or internal deploy, `SWITCHEROO_REQUIRE_HARDENED`, reverse proxy / TLS
- Auth, sessions, CSRF, cookies, login lockout, password hashing
- Device/TACACS secrets, `.env`, SQLite file theft
- Teams / ServiceNow webhooks (SSRF, credential-in-URL)
- The operator is not a security expert and wants known-good defaults

Do **not** use this skill to write exploits, attack playbooks, or "prove it is vulnerable" payloads. Fix and document. Point to OWASP concepts without reproduction steps.

## How to work

1. Read `docs/security.md` and this skill. Treat the human-facing brief as the source of truth for Cyber; keep it in sync with code.
2. Prefer **fail-fast at startup** (`app/prereq.py`) over silent insecure defaults.
3. Every new control needs: code, a test in `tests/test_security.py` (or a focused sibling), an `.env.example` knob if configurable, and a line in `docs/security.md`.
4. Lab convenience stays on loopback with the documented lab key. Shared deploy uses `SWITCHEROO_REQUIRE_HARDENED=true`, which must **refuse** to start until secrets, HTTPS cookies, allowed hosts, and a non-lab first user are in place.
5. After changing hardening, run `python3 -m pytest --timeout=30 --timeout-method=thread`.

## What Switcheroo already does

Do not re-implement these. Extend them if they are incomplete.

| Control | Where |
| --- | --- |
| Loopback bind; refuse `0.0.0.0` with the lab session key | `app/prereq.py`, `SWITCHEROO_HOST` |
| FastAPI `/docs` and `/redoc` off | `app/main.py` |
| Session cookie HttpOnly, SameSite=Lax, optional Secure, 8h max-age | `SessionMiddleware` in `app/main.py` |
| Session ID rotated on login (clear then set `user_id`) | `app/routers/pages.py` |
| CSRF on POST (hidden field + `X-CSRF-Token`); off in tests unless forced | `app/csrf.py` |
| Login lockout 8 / 15 min per IP; `X-Forwarded-For` ignored unless trusted | `app/rate_limit.py` |
| Dummy scrypt verify when the username is missing (timing) | `app/auth.py` |
| scrypt password hashes; legacy `pbkdf2$` still verifies | `app/auth.py` |
| Generic login error text | login routes |
| Open-redirect allowlist for `next` | `safe_next_path` |
| Fernet `enc:v1:` device passwords | `app/crypto.py` |
| Security headers (nosniff, DENY frames, CSP, Referrer-Policy, Permissions-Policy) | `app/security_headers.py` |
| HSTS when Secure cookies are on | `app/security_headers.py` |
| `Cache-Control: no-store` on non-static responses | `app/security_headers.py` |
| `TrustedHostMiddleware` when hosts are not `*` | `app/main.py`, `SWITCHEROO_ALLOWED_HOSTS` |
| Teams webhook host allowlist (HTTPS only) | `app/drivers/teams.py` |
| Parameterized SQLAlchemy; Jinja autoescape | models / templates |
| No CORS middleware | — |
| uvicorn `reload=False` | `app/__main__.py` |
| Python 3.12+ fail-fast; pinned `requirements.txt` including `cryptography` | `app/prereq.py` |
| Lab users `networks`/`cs` **not** created when hardened; bootstrap env instead | `app/seed.py` |
| SQLite / log / audit files `chmod 600` (Unix) | `app/filesec.py` |
| Auth and approval audit log (no secrets) | `app/audit.py` |
| Admin-created passwords minimum length 10 | `app/auth.py` |
| Login page hides lab passwords unless the lab session key is in use | `app/templates/login.html` |

## Checklist — close these if missing

Use this when auditing a branch or a fork. Items marked **must** are required before calling a shared host "hardened".

### Must (shared / internal host)

- [ ] `SWITCHEROO_SECRET_KEY` 32+ random, not the lab default
- [ ] `SWITCHEROO_DATA_KEY` 32+ random, **different** from the session key
- [ ] `SWITCHEROO_REQUIRE_HARDENED=true` (startup fails if the above, Secure cookies, or HTTPS public URL are wrong)
- [ ] TLS at a reverse proxy; `SWITCHEROO_PUBLIC_URL=https://…`; `SWITCHEROO_COOKIE_SECURE=true`
- [ ] `SWITCHEROO_ALLOWED_HOSTS` is the real hostname(s), no `*`
- [ ] Bind `127.0.0.1` and let the proxy connect locally, **or** `0.0.0.0` only with host firewall allowlisting the proxy
- [ ] First Networks user from `SWITCHEROO_BOOTSTRAP_PASSWORD` (12+), then change it under Access. Never ship `networks`/`networks` or `cs`/`cs` on a shared DB
- [ ] `.env` and `data/` ACL'd to the service account (NTFS on Windows; `0700`/`0600` on Unix)
- [ ] `CISCO_RESTCONF_VERIFY_TLS=true`; dedicated TACACS user, not a personal login
- [ ] Live Teams / ServiceNow only with dedicated integration credentials; webhook URL treated as a secret

### Should (in-app)

- [ ] CSRF on every cookie-authenticated POST
- [ ] Login lockout; do not trust `X-Forwarded-For` unless the proxy is known to overwrite it
- [ ] Rotate session on login; clear session on logout
- [ ] Constant-time-ish login (dummy hash for unknown user; `hmac.compare_digest` on hashes)
- [ ] Encrypted device secrets at rest; never log passwords, webhook `sig`, or session values
- [ ] Security headers + HSTS on HTTPS + `no-store` for HTML
- [ ] Trusted Host header
- [ ] Audit log: login success/fail, logout, user create, approve/reject, ack (request id + actor, no secrets)
- [ ] Password minimum length on admin-created users
- [ ] Hide lab default passwords on the login page outside lab mode

### Residual — document, do not fake

These are honest gaps. Write them in `docs/security.md`. Do not pretend the app has them.

| Gap | Why it stays |
| --- | --- |
| No Entra ID / SSO | v1 is local username/password until IAM delivers it |
| No in-process HTTPS | Reverse proxy terminates TLS; the app speaks HTTP on localhost |
| Starlette session is **signed, not encrypted** | Never put secrets in `request.session` |
| Absolute session lifetime only (default 8h), no idle timeout | Would need last-seen tracking; call out to Cyber |
| Login lockout is in-process memory | Resets on restart; not shared across workers |
| Networks can set `management_ip` | That role is trusted to talk to switches (SSRF-shaped by design) |
| Argon2id not used | stdlib scrypt is acceptable; do not add a crypto library without a pin |
| No pip-audit in CI | Operator runs `pip-audit -r requirements.txt` on a cadence |
| CSRF JS assist | `app.js` injects the token for HTMX; tests keep CSRF off unless a test turns it on |
| Enabling hardened does not delete existing lab users | Operator must change/delete `networks`/`cs` on an already-seeded DB |

## Adding a new control

1. Fail closed in `app/prereq.py` when `require_hardened` is on, if the control is a deploy-time setting.
2. Keep lab first-run working on `127.0.0.1` with the lab key.
3. Never log secrets. Hostnames are OK; webhook query strings are not.
4. Tests must not depend on wall-clock timing of scrypt. Assert behavior (dummy verify called, 403 without CSRF, 400 on bad Host).
5. `SWITCHEROO_TESTING=1` may disable CSRF and login rate limit so the rest of the suite stays simple. New tests that care about those controls must opt in via env.

## Research map (what people usually miss)

When the operator is not a security expert, check these even if they did not name them:

- **Session fixation** — regenerate session at login, not only "HttpOnly"
- **Host header / password-reset style attacks** — `TrustedHostMiddleware` + allowed hosts matching `PUBLIC_URL`
- **HSTS** — only when cookies are Secure / site is HTTPS, or browsers will remember HTTPS for a lab name
- **User enumeration timing** — hashing cost on unknown usernames
- **Well-known seed accounts** — the most common "we hardened cookies" miss
- **File modes** — encrypting SQLite is useless if `data/switcheroo.db` is world-readable
- **Cache** — authenticated HTML in browser/proxy cache
- **Bootstrap chicken-and-egg** — hardened mode cannot require Access UI to create the first user
- **Audit trail** — Cyber will ask who approved a VLAN change
- **Signed vs encrypted cookies** — people hear "session encryption" and mean HMAC
- **X-Forwarded-For spoofing** — lockout becomes useless if clients can send any IP
- **Open redirects** via `next=`
- **Webhook SSRF** — user-controlled URL with a `sig` query param
- **Dependency pins + SCA** — "up to date language" means runtime **and** pinned libs

Do not add: public internet exposure, in-app TLS, or "penetration test" scripts.

## Config knobs (keep `.env.example` aligned)

```
SWITCHEROO_REQUIRE_HARDENED=true
SWITCHEROO_SECRET_KEY=<32+ random>
SWITCHEROO_DATA_KEY=<32+ random, different>
SWITCHEROO_COOKIE_SECURE=true
SWITCHEROO_PUBLIC_URL=https://switcheroo.internal.example
SWITCHEROO_ALLOWED_HOSTS=switcheroo.internal.example,localhost
SWITCHEROO_BOOTSTRAP_USERNAME=networks
SWITCHEROO_BOOTSTRAP_PASSWORD=<12+ random, then change in Access>
SWITCHEROO_CSRF=true
SWITCHEROO_LOGIN_RATE_LIMIT=true
SWITCHEROO_TRUST_X_FORWARDED_FOR=false
```

Lab (loopback) may leave `REQUIRE_HARDENED=false` and `ALLOWED_HOSTS` unset (`*` — TrustedHost off). That mode is not a shared deploy.
