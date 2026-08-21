from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LAB_SECRET_KEY, Settings, get_settings
from app.models import User
from app.prereq import _host_allowed

Status = Literal["done", "action", "open"]

LAB_USERNAMES = ("networks", "cs")


@dataclass(frozen=True)
class CheckItem:
    id: str
    title: str
    detail: str
    status: Status


@dataclass(frozen=True)
class CheckSection:
    id: str
    title: str
    blurb: str
    items: tuple[CheckItem, ...]

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.status == "done")

    @property
    def action_count(self) -> int:
        return sum(1 for item in self.items if item.status == "action")

    @property
    def open_count(self) -> int:
        return sum(1 for item in self.items if item.status == "open")


@dataclass(frozen=True)
class SecurityReport:
    sections: tuple[CheckSection, ...]
    lab_mode: bool
    bind: str

    @property
    def done_count(self) -> int:
        return sum(section.done_count for section in self.sections)

    @property
    def action_count(self) -> int:
        return sum(section.action_count for section in self.sections)

    @property
    def open_count(self) -> int:
        return sum(section.open_count for section in self.sections)


def build_security_report(db: Session, settings: Settings | None = None) -> SecurityReport:
    """Live checklist for Help → Security. Never include secret values."""
    settings = settings or get_settings()
    lab_users = _lab_usernames_present(db)
    return SecurityReport(
        sections=(
            _product_section(),
            _host_section(settings, lab_users),
            _residual_section(),
        ),
        lab_mode=not settings.require_hardened,
        bind=f"{settings.host}:{settings.port}",
    )


def _lab_usernames_present(db: Session) -> tuple[str, ...]:
    found = db.scalars(select(User.username).where(User.username.in_(LAB_USERNAMES))).all()
    return tuple(sorted(found))


def _product_section() -> CheckSection:
    """Controls that ship in this build. Status is done unless the code is removed."""
    items = (
        CheckItem(
            "bind-default",
            "Loopback bind by default",
            "First run listens on 127.0.0.1. Binding 0.0.0.0 with the lab session key is refused at startup.",
            "done",
        ),
        CheckItem(
            "no-docs",
            "API docs are off",
            "FastAPI /docs and /redoc are disabled. /health does not return secrets.",
            "done",
        ),
        CheckItem(
            "authz",
            "Pages require a session; CS is scoped",
            "Unauthenticated users go to /login. Client Services only see granted switches. Networks-only routes return 403.",
            "done",
        ),
        CheckItem(
            "approvals",
            "Writes wait for Networks approval",
            "VLAN, bounce, and no-shutdown queue until approve (unless an auto-approve policy is on).",
            "done",
        ),
        CheckItem(
            "passwords",
            "Login passwords are scrypt hashes",
            "Unique salt per user. Legacy pbkdf2$ hashes still verify. Unknown usernames still pay a dummy scrypt cost. Admin-created passwords must be 10+ characters.",
            "done",
        ),
        CheckItem(
            "session",
            "Session cookie is HttpOnly + SameSite=Lax",
            "Rotated on login (session fixation). Cleared on logout. Signed, not encrypted — nothing secret is stored in it. Default max age 8 hours.",
            "done",
        ),
        CheckItem(
            "csrf-lockout",
            "CSRF tokens and login lockout exist",
            "POST needs a CSRF token (hidden field or X-CSRF-Token). Lockout is 8 failures / 15 minutes per IP. X-Forwarded-For is ignored unless explicitly trusted.",
            "done",
        ),
        CheckItem(
            "device-secrets",
            "Device / TACACS passwords are encrypted at rest",
            "SQLite stores Fernet enc:v1: blobs keyed by SWITCHEROO_DATA_KEY (or the session key if unset). .env is gitignored.",
            "done",
        ),
        CheckItem(
            "headers",
            "Security headers, no-store, HSTS when HTTPS",
            "nosniff, DENY frames, CSP, Referrer-Policy, Permissions-Policy. Authenticated pages send Cache-Control: no-store. HSTS is set when Secure cookies are on.",
            "done",
        ),
        CheckItem(
            "ssrf-redirect",
            "Open redirects and Teams webhook SSRF are allowlisted",
            "Login/approve next= must be a same-origin relative path. Live Teams webhooks must be https on Teams / Power Automate hosts.",
            "done",
        ),
        CheckItem(
            "audit",
            "Audit log without secrets",
            "data/audit.log records login, logout, user create, request create/approve/reject/ack. Passwords, tokens, and webhook URLs are not written.",
            "done",
        ),
        CheckItem(
            "integrations",
            "Teams and ServiceNow default to dry-run",
            "No live HTTP until enabled with credentials. Python 3.12+ is required. Dependencies are pinned.",
            "done",
        ),
    )
    return CheckSection(
        "product",
        "Already in this release",
        "These shipped in the app. They do not depend on this host’s .env.",
        items,
    )


def _host_section(settings: Settings, lab_users: tuple[str, ...]) -> CheckSection:
    secret_ok = settings.secret_key != LAB_SECRET_KEY and len(settings.secret_key) >= 32
    data_ok = bool(settings.data_key) and len(settings.data_key) >= 32 and settings.data_key != settings.secret_key
    public_https = bool(settings.public_url) and settings.public_url.startswith("https://")
    hosts_ok = bool(settings.allowed_hosts) and "*" not in settings.allowed_hosts
    public_host = settings.public_hostname
    host_matches = (not public_host) or _host_allowed(public_host, settings.allowed_hosts)
    bind_ok = (not settings.bind_is_all_interfaces) or secret_ok
    lab_users_ok = len(lab_users) == 0

    if settings.csrf_enabled:
        csrf_detail = "On."
        csrf_status: Status = "done"
    elif settings.testing:
        csrf_detail = "Off while SWITCHEROO_TESTING=1 so automated tests stay simple. Leave SWITCHEROO_CSRF=true on a real host."
        csrf_status = "action"
    else:
        csrf_detail = "SWITCHEROO_CSRF is false. Turn it on for any shared host."
        csrf_status = "action"

    if settings.login_rate_limit:
        lock_detail = "On (8 / 15 min per IP)."
        lock_status: Status = "done"
    elif settings.testing:
        lock_detail = "Off while SWITCHEROO_TESTING=1. Leave SWITCHEROO_LOGIN_RATE_LIMIT=true on a real host."
        lock_status = "action"
    else:
        lock_detail = "SWITCHEROO_LOGIN_RATE_LIMIT is false. Turn it on for any shared host."
        lock_status = "action"

    if lab_users_ok:
        lab_detail = "This database has no users named networks or cs."
    elif settings.require_hardened:
        lab_detail = (
            "Users still named "
            + ", ".join(lab_users)
            + ". Hardened mode does not delete them — change those passwords under Access if they are still the README values."
        )
    else:
        lab_detail = (
            "Lab users present ("
            + ", ".join(lab_users)
            + "). Change or delete them under Access before any shared deploy."
        )

    if settings.bind_is_all_interfaces:
        bind_detail = (
            f"Listening on {settings.host}:{settings.port} (all interfaces). "
            "Restrict with a host firewall or reverse proxy. Do not put this on the public internet."
        )
    else:
        bind_detail = f"Listening on {settings.host}:{settings.port} (loopback). A reverse proxy can connect locally."

    if hosts_ok and host_matches:
        host_detail = "Allowed hosts: " + ", ".join(settings.allowed_hosts) + "."
        host_status: Status = "done"
    elif not hosts_ok:
        host_detail = "SWITCHEROO_ALLOWED_HOSTS is unset or wildcard. Set the real hostname before a shared deploy."
        host_status = "action"
    else:
        host_detail = (
            f"SWITCHEROO_PUBLIC_URL host '{public_host}' is not in SWITCHEROO_ALLOWED_HOSTS "
            f"({', '.join(settings.allowed_hosts)})."
        )
        host_status = "action"

    items = (
        CheckItem(
            "require-hardened",
            "Hardened mode",
            "SWITCHEROO_REQUIRE_HARDENED=true — startup fails if lab secrets, cleartext public URL, or Secure cookies are missing."
            if settings.require_hardened
            else "Off. Set SWITCHEROO_REQUIRE_HARDENED=true before a shared/internal host.",
            "done" if settings.require_hardened else "action",
        ),
        CheckItem(
            "secret-key",
            "Session signing key",
            "SWITCHEROO_SECRET_KEY is 32+ characters and is not the lab default."
            if secret_ok
            else "Still the lab default or shorter than 32 characters. Generate a new random value.",
            "done" if secret_ok else "action",
        ),
        CheckItem(
            "data-key",
            "Device-secret encryption key",
            "SWITCHEROO_DATA_KEY is 32+ characters and different from the session key."
            if data_ok
            else "Unset, too short, or the same as SWITCHEROO_SECRET_KEY. Set a dedicated 32+ random string.",
            "done" if data_ok else "action",
        ),
        CheckItem(
            "tls-cookies",
            "HTTPS public URL and Secure cookies",
            (
                f"Public URL is {settings.public_url}. Secure cookies are on. HSTS is sent."
                if public_https and settings.cookie_secure
                else "Put TLS on the reverse proxy, set SWITCHEROO_PUBLIC_URL=https://… and SWITCHEROO_COOKIE_SECURE=true."
            ),
            "done" if public_https and settings.cookie_secure else "action",
        ),
        CheckItem(
            "allowed-hosts",
            "Allowed Host headers",
            host_detail,
            host_status,
        ),
        CheckItem(
            "bind",
            "Bind address",
            bind_detail,
            "done" if bind_ok else "action",
        ),
        CheckItem(
            "csrf-on",
            "CSRF is on for this process",
            csrf_detail,
            csrf_status,
        ),
        CheckItem(
            "lockout-on",
            "Login lockout is on for this process",
            lock_detail,
            lock_status,
        ),
        CheckItem(
            "xff",
            "X-Forwarded-For is not blindly trusted",
            "Ignored (correct unless a known reverse proxy overwrites the header)."
            if not settings.trust_x_forwarded_for
            else "SWITCHEROO_TRUST_X_FORWARDED_FOR=true. Confirm the proxy overwrites X-Forwarded-For or lockout can be spoofed.",
            "done" if not settings.trust_x_forwarded_for else "action",
        ),
        CheckItem(
            "restconf-tls",
            "RESTCONF TLS verify",
            "CISCO_RESTCONF_VERIFY_TLS is on."
            if settings.cisco_restconf_verify_tls
            else "CISCO_RESTCONF_VERIFY_TLS is false. Turn it on for real switches.",
            "done" if settings.cisco_restconf_verify_tls else "action",
        ),
        CheckItem(
            "lab-users",
            "No published lab usernames",
            lab_detail,
            "done" if lab_users_ok else "action",
        ),
        CheckItem(
            "lab-login-hint",
            "Login page does not show lab passwords",
            "Lab passwords are hidden on /login."
            if not settings.show_lab_credentials
            else "Lab passwords are printed on /login because this process still uses the lab session key. That is only for a first run on this machine.",
            "done" if not settings.show_lab_credentials else "action",
        ),
    )
    return CheckSection(
        "host",
        "This host still needs",
        "Live read of the running process. Items marked Needs action must be fixed before calling this a shared deploy. Secret values are never shown.",
        items,
    )


def _residual_section() -> CheckSection:
    items = (
        CheckItem(
            "sso",
            "Entra ID / SSO",
            "v1 is local username/password. IAM has to deliver SSO before this can change.",
            "open",
        ),
        CheckItem(
            "in-process-tls",
            "HTTPS inside the Python process",
            "The app speaks HTTP on localhost. Terminate TLS on IIS / nginx / Caddy in front.",
            "open",
        ),
        CheckItem(
            "idle-timeout",
            "Idle session timeout",
            "Cookies last until the absolute max age (default 8 hours) or Sign out. There is no inactivity timer yet.",
            "open",
        ),
        CheckItem(
            "lockout-memory",
            "Lockout is in-process memory",
            "Counters reset on restart and are not shared across workers. Fine for a single service process.",
            "open",
        ),
        CheckItem(
            "signed-session",
            "Session cookie is signed, not encrypted",
            "Starlette HMAC-signs the cookie. Do not put secrets in the session. Device passwords are Fernet in SQLite, not in the cookie.",
            "open",
        ),
        CheckItem(
            "mgmt-ip",
            "Networks can set any management IP",
            "That role is trusted to talk to Catalysts. Treat it as admin-equivalent for device targeting.",
            "open",
        ),
        CheckItem(
            "pip-audit",
            "Dependency scanning is not in CI",
            "Pins live in requirements.txt. Run pip-audit -r requirements.txt (or the org SCA tool) before go-live and on a cadence.",
            "open",
        ),
        CheckItem(
            "file-acls",
            "Windows NTFS ACLs on .env and data\\",
            "Unix chmod 0700/0600 is applied when the OS allows it. On Windows, restrict those paths to the service account by hand.",
            "open",
        ),
    )
    return CheckSection(
        "residual",
        "Still open (honest gaps)",
        "Not missing by accident. Do not pretend the app has these. Cyber should treat them as residual risk.",
        items,
    )
