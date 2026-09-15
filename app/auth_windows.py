from __future__ import annotations

import logging
import time
from typing import Protocol

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import Settings, get_settings
from app.csrf import ensure_csrf_token
from app.models import AUTH_WINDOWS, ROLE_CS, ROLE_NETWORKS, User, utcnow

log = logging.getLogger("switcheroo.auth.windows")

WINDOWS_PASSWORD_MARKER = "windows$"

_LOOPBACK_PEERS = {"127.0.0.1", "::1", "localhost", "testclient", "testserver"}

_resolver: "AdGroupResolver | None" = None


class AdGroupResolver(Protocol):
    def groups_for(self, account: str) -> list[str]:
        """Return AD/local group names for a Windows account. Never raise to the web tier."""


class MappingGroupResolver:
    """Injectable resolver for tests and lab fakes. Keys are case-insensitive accounts."""

    def __init__(self, mapping: dict[str, list[str]] | None = None):
        self.mapping = {k.lower(): list(v) for k, v in (mapping or {}).items()}

    def groups_for(self, account: str) -> list[str]:
        return list(self.mapping.get(account.lower(), []))


class EmptyGroupResolver:
    def groups_for(self, account: str) -> list[str]:
        return []


class Win32GroupResolver:
    def groups_for(self, account: str) -> list[str]:
        try:
            import win32net  # type: ignore[import-not-found]
        except ImportError:
            log.warning("win32net is not available; Windows group lookup returned nothing")
            return []
        domain, _, sam = account.partition("\\")
        if not sam:
            sam = account
            domain = None
        names: list[str] = []
        try:
            groups, _total, _resume = win32net.NetUserGetGroups(domain or None, sam)
            for item in groups:
                name = item[0] if item else ""
                if name:
                    names.append(f"{domain}\\{name}" if domain else str(name))
        except Exception:
            log.exception("NetUserGetGroups failed for %s", account)
        try:
            local, _total, _resume = win32net.NetUserGetLocalGroups(domain or None, sam)
            for item in local:
                name = item[0] if isinstance(item, tuple) else str(item)
                if name:
                    names.append(str(name))
        except Exception:
            log.exception("NetUserGetLocalGroups failed for %s", account)
        return names


class LdapGroupResolver:
    def __init__(self, url: str):
        self.url = url

    def groups_for(self, account: str) -> list[str]:
        try:
            from ldap3 import SUBTREE, Connection, Server  # type: ignore[import-not-found]
        except ImportError:
            log.warning("ldap3 is not installed; SWITCHEROO_AD_LDAP_URL is ignored")
            return []
        sam = account.split("\\")[-1].split("@")[0]
        if not sam:
            return []
        try:
            server = Server(self.url, connect_timeout=5)
            conn = Connection(server, auto_bind=True, receive_timeout=5)
            conn.search(
                search_base="",
                search_filter=f"(sAMAccountName={_ldap_escape(sam)})",
                search_scope=SUBTREE,
                attributes=["memberOf"],
                size_limit=1,
            )
            if not conn.entries:
                return []
            raw = conn.entries[0].memberOf.values if hasattr(conn.entries[0], "memberOf") else []
            names: list[str] = []
            for dn in raw:
                text = str(dn)
                if text.upper().startswith("CN="):
                    names.append(text.split(",")[0][3:])
                else:
                    names.append(text)
            return names
        except Exception:
            log.exception("LDAP group lookup failed for %s", account)
            return []


def _ldap_escape(value: str) -> str:
    return (
        value.replace("\\", r"\5c")
        .replace("*", r"\2a")
        .replace("(", r"\28")
        .replace(")", r"\29")
        .replace("\x00", r"\00")
    )


def set_group_resolver(resolver: AdGroupResolver | None) -> None:
    global _resolver
    _resolver = resolver


def reset_group_resolver() -> None:
    set_group_resolver(None)


def get_group_resolver(settings: Settings | None = None) -> AdGroupResolver:
    if _resolver is not None:
        return _resolver
    settings = settings or get_settings()
    if settings.ad_ldap_url:
        return LdapGroupResolver(settings.ad_ldap_url)
    return Win32GroupResolver()


def parse_windows_identity(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text or len(text) > 128:
        return None
    if any(ord(ch) < 32 for ch in text):
        return None
    text = text.replace("/", "\\")
    if "\\" in text:
        domain, _, sam = text.partition("\\")
        if not domain or not sam or "\\" in sam:
            return None
        return f"{domain}\\{sam}"
    return text


def peer_may_assert_windows_identity(request: Request, settings: Settings | None = None) -> bool:
    """Only IIS on loopback (or a trusted proxy that overwrites XFF) may send the user header."""
    settings = settings or get_settings()
    host = request.client.host if request.client else ""
    if host in _LOOPBACK_PEERS:
        return True
    return bool(settings.trust_x_forwarded_for)


def _norm_group(name: str) -> str:
    return name.strip().lower().replace("/", "\\")


def _group_matches(have: list[str], wanted: tuple[str, ...]) -> bool:
    have_n = {_norm_group(item) for item in have}
    have_short = {item.split("\\")[-1] for item in have_n}
    for item in wanted:
        needle = _norm_group(item)
        if needle in have_n or needle.split("\\")[-1] in have_short:
            return True
    return False


def role_from_groups(groups: list[str], settings: Settings | None = None) -> str | None:
    settings = settings or get_settings()
    if _group_matches(groups, settings.ad_networks_groups):
        return ROLE_NETWORKS
    if _group_matches(groups, settings.ad_cs_groups):
        return ROLE_CS
    return None


def find_user_by_account(db: Session, account: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.username) == account.lower()))


def upsert_windows_user(db: Session, account: str, role: str) -> User:
    user = find_user_by_account(db, account)
    sam = account.split("\\")[-1]
    if user is None:
        user = User(
            username=account[:64],
            password_hash=WINDOWS_PASSWORD_MARKER,
            role=role,
            display_name=sam,
            auth_source=AUTH_WINDOWS,
            is_active=True,
        )
        db.add(user)
        db.flush()
        audit("user_created", username=account[:64], role=role, actor="windows-jit")
    else:
        if user.role != role:
            audit("windows_role_changed", username=user.username, from_role=user.role, to_role=role)
            user.role = role
        user.auth_source = AUTH_WINDOWS
        if not user.display_name:
            user.display_name = sam
    user.last_login_at = utcnow()
    db.flush()
    return user


def groups_check_is_fresh(request: Request, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    stamped = request.session.get("groups_checked_at")
    if not isinstance(stamped, (int, float)):
        return False
    return (time.time() - stamped) < settings.ad_group_ttl_seconds


def apply_windows_identity(db: Session, request: Request) -> User | None:
    """JIT or refresh a session from a trusted IIS identity header. None = no change."""
    settings = get_settings()
    if not settings.windows_auth:
        return None
    if not peer_may_assert_windows_identity(request, settings):
        return None
    account = parse_windows_identity(request.headers.get(settings.windows_user_header))
    if not account:
        return None
    groups = get_group_resolver(settings).groups_for(account)
    role = role_from_groups(groups, settings)
    existing_id = request.session.get("user_id")
    existing = db.get(User, int(existing_id)) if existing_id else None
    if existing is not None and existing.username.lower() == account.lower() and groups_check_is_fresh(request, settings):
        return existing if existing.is_active else None
    if role is None:
        if existing is not None and existing.username.lower() == account.lower():
            existing.is_active = False
            db.commit()
            audit("windows_revoked", username=existing.username)
            request.session.clear()
        else:
            audit("windows_denied", username=account[:64])
        return None
    user = upsert_windows_user(db, account, role)
    if not user.is_active:
        return None
    same = existing is not None and existing.id == user.id
    if not same:
        request.session.clear()
        request.session["user_id"] = user.id
        ensure_csrf_token(request)
        audit("windows_login", username=user.username, user_id=user.id, role=user.role)
    request.session["windows_account"] = user.username
    request.session["groups_checked_at"] = time.time()
    request.session["last_seen"] = time.time()
    db.commit()
    return user
