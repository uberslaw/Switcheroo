from __future__ import annotations

import base64
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings

log = logging.getLogger("switcheroo.crypto")

PREFIX = "enc:v1:"
_HKDF_SALT = b"switcheroo-device-secrets-v1"
_HKDF_INFO = b"fernet"


class SecretError(Exception):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    material = (settings.data_key or settings.secret_key).encode("utf-8")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    ).derive(material)
    return Fernet(base64.urlsafe_b64encode(key))


def store_secret(plaintext: str | None) -> Optional[str]:
    """Encrypt a device/integration secret for SQLite. Empty stays empty."""
    text = (plaintext or "").strip()
    if not text:
        return None
    token = _fernet().encrypt(text.encode("utf-8")).decode("ascii")
    return PREFIX + token


def reveal_secret(stored: str | None) -> Optional[str]:
    """Decrypt enc:v1: blobs. Legacy plaintext is returned as-is (then re-encrypted on save)."""
    text = stored or ""
    if not text:
        return None
    if not text.startswith(PREFIX):
        return text
    blob = text[len(PREFIX) :]
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretError(
            "Could not decrypt a device secret. SWITCHEROO_DATA_KEY / SWITCHEROO_SECRET_KEY "
            "does not match the key used to encrypt it."
        ) from exc


def secret_is_stored(stored: str | None) -> bool:
    return bool(stored)
