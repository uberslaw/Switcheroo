from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_DOTENV_PATH = PROJECT_ROOT / ".env"
if _DOTENV_PATH.exists() and os.getenv("SWITCHEROO_TESTING") != "1":
    load_dotenv(_DOTENV_PATH)


LAB_SECRET_KEY = "change-me-lab-only-not-for-production"


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _teams_format(value: str | None) -> str:
    text = (value or "adaptive").strip().lower()
    if text not in {"adaptive", "messagecard"}:
        raise ValueError("TEAMS_WEBHOOK_FORMAT must be 'adaptive' or 'messagecard'.")
    return text


def _as_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


def _resolve_path(raw: str, default_relative: str) -> Path:
    text = (raw or default_relative).strip()
    path = Path(text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    testing: bool
    driver: str
    host: str
    port: int
    secret_key: str
    data_dir: Path
    database_url: str
    log_file: Path
    status_poll_interval: int
    on_demand_cooldown: int
    troubleshoot_interval: int
    troubleshoot_duration: int
    daily_poll_hour: int
    sim_flaps: bool
    servicenow_enabled: bool
    servicenow_dry_run: bool
    servicenow_instance_url: str
    servicenow_table: str
    servicenow_username: str
    servicenow_password: str
    servicenow_assignment_group: str
    servicenow_poll_seconds: int
    servicenow_state_resolved: str
    servicenow_state_cancelled: str
    servicenow_close_code: str
    servicenow_http_timeout: int
    public_url: str
    teams_enabled: bool
    teams_dry_run: bool
    teams_webhook_url: str
    teams_webhook_format: str
    teams_http_timeout: int
    cisco_restconf_port: int
    cisco_restconf_verify_tls: bool
    cisco_netconf_port: int
    cisco_ssh_port: int
    cisco_snmp_community: str
    cisco_snmp_port: int
    cisco_connect_timeout: int
    data_key: str
    csrf_enabled: bool
    login_rate_limit: bool
    cookie_secure: bool
    session_max_age: int
    trust_x_forwarded_for: bool
    require_hardened: bool
    allowed_hosts: tuple[str, ...]
    bootstrap_username: str
    bootstrap_password: str

    @property
    def servicenow_live(self) -> bool:
        return self.servicenow_enabled and not self.servicenow_dry_run

    @property
    def teams_live(self) -> bool:
        return self.teams_enabled and not self.teams_dry_run

    @property
    def bind_is_all_interfaces(self) -> bool:
        return self.host in {"0.0.0.0", "::"}

    @property
    def show_lab_credentials(self) -> bool:
        """Login page / stdout may print lab passwords only in first-run lab mode."""
        return (not self.require_hardened) and self.secret_key == LAB_SECRET_KEY

    @property
    def trusted_hosts_enabled(self) -> bool:
        return "*" not in self.allowed_hosts

    @property
    def public_hostname(self) -> str:
        if not self.public_url:
            return ""
        return (urlparse(self.public_url).hostname or "").strip().lower()


def get_settings() -> Settings:
    testing = os.getenv("SWITCHEROO_TESTING") == "1"
    data_dir = _resolve_path(os.getenv("SWITCHEROO_DATA_DIR", "data"), "data")
    log_file = _resolve_path(os.getenv("SWITCHEROO_LOG_FILE", "data/switcheroo.log"), "data/switcheroo.log")
    db_url = (os.getenv("SWITCHEROO_DATABASE_URL") or "").strip()
    if not db_url:
        db_path = (data_dir / "switcheroo.db").resolve()
        db_url = "sqlite:///" + db_path.as_posix()

    interval = _as_int(os.getenv("SWITCHEROO_STATUS_POLL_INTERVAL"), 60)
    if not testing and not (60 <= interval <= 180):
        raise ValueError(
            "SWITCHEROO_STATUS_POLL_INTERVAL must be between 60 and 180 seconds "
            f"(got {interval}). Targeted ifOperStatus polls only — do not lower this."
        )
    if testing and interval < 1:
        interval = 60

    cooldown = _as_int(os.getenv("SWITCHEROO_ON_DEMAND_COOLDOWN"), 60)
    ts_interval = _as_int(os.getenv("SWITCHEROO_TROUBLESHOOT_INTERVAL"), 10)
    ts_duration = _as_int(os.getenv("SWITCHEROO_TROUBLESHOOT_DURATION"), 300)
    daily_hour = _as_int(os.getenv("SWITCHEROO_DAILY_POLL_HOUR"), 2)
    if daily_hour < 0 or daily_hour > 23:
        raise ValueError("SWITCHEROO_DAILY_POLL_HOUR must be 0-23.")

    driver = (os.getenv("SWITCHEROO_DRIVER") or "simulator").strip().lower()
    if driver not in {"simulator", "cisco_iosxe"}:
        raise ValueError("SWITCHEROO_DRIVER must be 'simulator' or 'cisco_iosxe'.")

    secret = os.getenv("SWITCHEROO_SECRET_KEY") or LAB_SECRET_KEY
    public_url = (os.getenv("SWITCHEROO_PUBLIC_URL") or "").strip().rstrip("/")
    cookie_secure = _as_bool(os.getenv("SWITCHEROO_COOKIE_SECURE"), public_url.startswith("https://"))
    require_hardened = _as_bool(os.getenv("SWITCHEROO_REQUIRE_HARDENED"), False)

    return Settings(
        testing=testing,
        driver=driver,
        host=os.getenv("SWITCHEROO_HOST", "127.0.0.1"),
        port=_as_int(os.getenv("SWITCHEROO_PORT"), 8080),
        secret_key=secret,
        data_dir=data_dir,
        database_url=db_url,
        log_file=log_file,
        status_poll_interval=interval,
        on_demand_cooldown=cooldown,
        troubleshoot_interval=ts_interval,
        troubleshoot_duration=ts_duration,
        daily_poll_hour=daily_hour,
        sim_flaps=_as_bool(os.getenv("SWITCHEROO_SIM_FLAPS"), True) and not testing,
        servicenow_enabled=_as_bool(os.getenv("SERVICENOW_ENABLED"), False),
        servicenow_dry_run=_as_bool(os.getenv("SERVICENOW_DRY_RUN"), True),
        servicenow_instance_url=(
            os.getenv("SERVICENOW_INSTANCE") or os.getenv("SERVICENOW_INSTANCE_URL") or ""
        ).strip().rstrip("/"),
        servicenow_table=(os.getenv("SERVICENOW_TABLE") or "incident").strip() or "incident",
        servicenow_username=(os.getenv("SERVICENOW_USERNAME") or "").strip(),
        servicenow_password=os.getenv("SERVICENOW_PASSWORD") or "",
        servicenow_assignment_group=(os.getenv("SERVICENOW_ASSIGNMENT_GROUP") or "").strip(),
        servicenow_poll_seconds=max(60, min(300, _as_int(os.getenv("SERVICENOW_POLL_SECONDS"), 120))),
        servicenow_state_resolved=(os.getenv("SERVICENOW_STATE_RESOLVED") or "6").strip(),
        servicenow_state_cancelled=(os.getenv("SERVICENOW_STATE_CANCELLED") or "8").strip(),
        servicenow_close_code=(os.getenv("SERVICENOW_CLOSE_CODE") or "Solved (Permanently)").strip(),
        servicenow_http_timeout=max(1, min(15, _as_int(os.getenv("SERVICENOW_HTTP_TIMEOUT"), 10))),
        public_url=public_url,
        teams_enabled=_as_bool(os.getenv("TEAMS_ENABLED"), False),
        teams_dry_run=_as_bool(os.getenv("TEAMS_DRY_RUN"), True),
        teams_webhook_url=(os.getenv("TEAMS_WEBHOOK_URL") or "").strip(),
        teams_webhook_format=_teams_format(os.getenv("TEAMS_WEBHOOK_FORMAT")),
        teams_http_timeout=max(1, min(15, _as_int(os.getenv("TEAMS_HTTP_TIMEOUT"), 10))),
        cisco_restconf_port=_as_int(os.getenv("CISCO_RESTCONF_PORT"), 443),
        cisco_restconf_verify_tls=_as_bool(os.getenv("CISCO_RESTCONF_VERIFY_TLS"), True),
        cisco_netconf_port=_as_int(os.getenv("CISCO_NETCONF_PORT"), 830),
        cisco_ssh_port=_as_int(os.getenv("CISCO_SSH_PORT"), 22),
        cisco_snmp_community=os.getenv("CISCO_SNMP_COMMUNITY") or "",
        cisco_snmp_port=_as_int(os.getenv("CISCO_SNMP_PORT"), 161),
        cisco_connect_timeout=_as_int(os.getenv("CISCO_CONNECT_TIMEOUT"), 10),
        data_key=(os.getenv("SWITCHEROO_DATA_KEY") or "").strip(),
        csrf_enabled=_as_bool(os.getenv("SWITCHEROO_CSRF"), not testing),
        login_rate_limit=_as_bool(os.getenv("SWITCHEROO_LOGIN_RATE_LIMIT"), not testing),
        cookie_secure=cookie_secure,
        session_max_age=max(300, min(86400, _as_int(os.getenv("SWITCHEROO_SESSION_MAX_AGE"), 28800))),
        trust_x_forwarded_for=_as_bool(os.getenv("SWITCHEROO_TRUST_X_FORWARDED_FOR"), False),
        require_hardened=require_hardened,
        allowed_hosts=_parse_allowed_hosts(
            os.getenv("SWITCHEROO_ALLOWED_HOSTS"),
            testing=testing,
            require_hardened=require_hardened,
        ),
        bootstrap_username=(os.getenv("SWITCHEROO_BOOTSTRAP_USERNAME") or "networks").strip() or "networks",
        bootstrap_password=os.getenv("SWITCHEROO_BOOTSTRAP_PASSWORD") or "",
    )


def _parse_allowed_hosts(
    raw: str | None, *, testing: bool, require_hardened: bool
) -> tuple[str, ...]:
    """Lab default is '*' (TrustedHost off). Hardened deploys must set an explicit list."""
    if raw is None or raw.strip() == "":
        if require_hardened:
            return ()
        if testing:
            return ("testserver", "test", "localhost", "127.0.0.1", "[::1]")
        return ("*",)
    hosts = tuple(part.strip() for part in raw.split(",") if part.strip())
    if testing and "*" not in hosts and "testserver" not in {h.lower() for h in hosts}:
        hosts = hosts + ("testserver", "test")
    return hosts
