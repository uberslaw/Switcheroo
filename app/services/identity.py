from __future__ import annotations

import getpass
import os

from app.models import User


def process_windows_account() -> str:
    """Identity of the OS process running Switcheroo (service account on a shared host)."""
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    username = (os.environ.get("USERNAME") or "").strip()
    if domain and username:
        return f"{domain}\\{username}"
    return getpass.getuser()


def current_windows_account(user: User | None = None) -> str:
    """Actor recorded on VLAN tickets: the signed-in user when known, else the process."""
    if user is not None and (user.username or "").strip():
        return user.username
    return process_windows_account()
