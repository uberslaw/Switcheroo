from __future__ import annotations

import getpass
import os


def current_windows_account() -> str:
    """Identity of the OS process running Switcheroo, not a browser SSO user.

    Local POC: USERDOMAIN\\USERNAME of the person who launched the app.
    A shared server would need SSO, or this field would be the service account.
    """
    domain = (os.environ.get("USERDOMAIN") or "").strip()
    username = (os.environ.get("USERNAME") or "").strip()
    if domain and username:
        return f"{domain}\\{username}"
    return getpass.getuser()
