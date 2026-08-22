from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from app.config import Settings
from app.filesec import restrict_private_dir, sqlite_filesystem_path

MIN_PYTHON = (3, 12)


class PrerequisiteError(SystemExit):
    """Raised (as SystemExit) when the process cannot start safely."""


def check_prerequisites(settings: Settings) -> None:
    """Fail fast with a clear message before the web app or poller starts."""
    if sys.version_info < MIN_PYTHON:
        raise PrerequisiteError(
            f"Switcheroo requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer. "
            f"This interpreter is {sys.version.split()[0]}. "
            "Install a supported Python and recreate the virtual environment."
        )

    data_dir: Path = settings.data_dir
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        restrict_private_dir(data_dir)
    except OSError as exc:
        raise PrerequisiteError(
            f"Cannot create data directory '{data_dir}': {exc}. "
            "Check NTFS permissions or set SWITCHEROO_DATA_DIR to a writable path."
        ) from exc

    probe = data_dir / ".write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise PrerequisiteError(
            f"Data directory '{data_dir}' is not writable: {exc}."
        ) from exc

    log_parent = settings.log_file.parent
    try:
        log_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PrerequisiteError(
            f"Cannot create log directory '{log_parent}': {exc}."
        ) from exc

    if settings.database_url.startswith("sqlite"):
        _check_sqlite(settings.database_url)

    _check_servicenow(settings)
    _check_teams(settings)
    _check_hardening(settings)


def _check_servicenow(settings: Settings) -> None:
    """Live Table API requires instance + dedicated user. Dry-run never calls SN."""
    if not settings.servicenow_enabled or settings.servicenow_dry_run:
        return
    missing: list[str] = []
    if not settings.servicenow_instance_url:
        missing.append("SERVICENOW_INSTANCE")
    if not settings.servicenow_username:
        missing.append("SERVICENOW_USERNAME")
    if not settings.servicenow_password:
        missing.append("SERVICENOW_PASSWORD")
    if missing:
        raise PrerequisiteError(
            "SERVICENOW_ENABLED=true and SERVICENOW_DRY_RUN=false but credentials are missing: "
            + ", ".join(missing)
            + ". Set a dedicated integration user (not a personal login), or set "
            "SERVICENOW_DRY_RUN=true until IAM issues the account. "
            "Switcheroo will not call ServiceNow anonymously. See docs/servicenow-poc.md."
        )


def _check_teams(settings: Settings) -> None:
    """Live webhook requires an https Teams/Power Automate URL and a public site link."""
    from app.drivers.teams import validate_teams_webhook_url

    if settings.teams_webhook_url:
        err = validate_teams_webhook_url(settings.teams_webhook_url)
        if err:
            raise PrerequisiteError(err)
    if not settings.teams_enabled or settings.teams_dry_run:
        return
    missing: list[str] = []
    if not settings.teams_webhook_url:
        missing.append("TEAMS_WEBHOOK_URL")
    if not settings.public_url:
        missing.append("SWITCHEROO_PUBLIC_URL")
    if missing:
        raise PrerequisiteError(
            "TEAMS_ENABLED=true and TEAMS_DRY_RUN=false but configuration is missing: "
            + ", ".join(missing)
            + ". Create a channel Workflows webhook (or Incoming Webhook) and set "
            "SWITCHEROO_PUBLIC_URL to the URL Networks can open from Teams. "
            "See docs/teams-webhook.md."
        )


def _check_hardening(settings: Settings) -> None:
    from app.config import LAB_SECRET_KEY

    lab_secret = settings.secret_key == LAB_SECRET_KEY
    if settings.bind_is_all_interfaces and lab_secret and not settings.testing:
        msg = (
            "SWITCHEROO_HOST binds all interfaces with the lab SWITCHEROO_SECRET_KEY. "
            "Allowed in lab mode for a LAN host. Set a long random SWITCHEROO_SECRET_KEY "
            "and SWITCHEROO_REQUIRE_HARDENED=true before a shared/production host. "
            "See docs/security.md."
        )
        print(msg, file=sys.stderr)
        logging.getLogger("switcheroo").warning(msg)
    if not settings.require_hardened:
        return
    if lab_secret or len(settings.secret_key) < 32:
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_SECRET_KEY of at least "
            "32 characters (not the lab default). See docs/security.md."
        )
    if not settings.data_key or len(settings.data_key) < 32:
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_DATA_KEY (32+ characters) "
            "so device secrets stay encrypted if the session key is rotated. See docs/security.md."
        )
    if settings.public_url and not settings.public_url.startswith("https://"):
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_PUBLIC_URL to be https:// "
            "when set, so Teams links and cookies are not sent over cleartext HTTP."
        )
    if not settings.cookie_secure:
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_COOKIE_SECURE=true "
            "(or an https SWITCHEROO_PUBLIC_URL) so session cookies are not sent over HTTP."
        )
    if not settings.allowed_hosts or "*" in settings.allowed_hosts:
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_ALLOWED_HOSTS "
            "(comma-separated hostnames, no wildcard). Include the hostname from "
            "SWITCHEROO_PUBLIC_URL. See docs/security.md."
        )
    public_host = settings.public_hostname
    if public_host and not _host_allowed(public_host, settings.allowed_hosts):
        raise PrerequisiteError(
            "SWITCHEROO_REQUIRE_HARDENED=true requires SWITCHEROO_ALLOWED_HOSTS to include "
            f"'{public_host}' from SWITCHEROO_PUBLIC_URL. See docs/security.md."
        )


def _host_allowed(hostname: str, allowed: tuple[str, ...]) -> bool:
    host = hostname.lower().rstrip(".")
    for pattern in allowed:
        item = pattern.lower().rstrip(".")
        if item == "*":
            return True
        if item.startswith("*."):
            suffix = item[1:]
            if host == item[2:] or host.endswith(suffix):
                return True
        elif host == item:
            return True
    return False


def _check_sqlite(url: str) -> None:
    from app.filesec import restrict_private_file

    path = sqlite_filesystem_path(url)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(path))
        conn.execute("SELECT 1")
        conn.close()
    except sqlite3.Error as exc:
        raise PrerequisiteError(
            f"SQLite file '{path}' is not usable: {exc}. "
            "Set SWITCHEROO_DATABASE_URL or SWITCHEROO_DATA_DIR to a writable location."
        ) from exc
    restrict_private_file(path)
