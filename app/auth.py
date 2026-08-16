from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ROLE_NETWORKS, User

PBKDF2_ROUNDS = 200_000
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        scheme = parts[0]
    except (ValueError, IndexError):
        return False
    if scheme == "scrypt":
        try:
            _scheme, n_s, r_s, p_s, salt_hex, hash_hex = parts
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n_s),
                r=int(r_s),
                p=int(p_s),
                dklen=len(bytes.fromhex(hash_hex)),
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(digest.hex(), hash_hex)
    if scheme == "pbkdf2":
        try:
            _scheme, salt_hex, hash_hex = parts
        except ValueError:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    return False


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    user = db.scalar(select(User).where(User.username == username))
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def user_from_request(db: Session, request: Request) -> Optional[User]:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.get(User, int(user_id))


def require_user(db: Session, request: Request) -> User:
    user = user_from_request(db, request)
    if user is None:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        location = "/login"
        if path and path != "/login":
            location = f"/login?next={quote(path, safe='')}"
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": location},
        )
    return user


def require_networks(user: User) -> User:
    if user.role != ROLE_NETWORKS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Networks role required")
    return user


def safe_next_path(raw: str | None, default: str = "/") -> str:
    """Allow only same-origin relative paths (login next, approve return)."""
    text = (raw or "").strip()
    if not text.startswith("/") or text.startswith("//") or "\\" in text or "://" in text:
        return default
    return text
