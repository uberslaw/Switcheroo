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


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$", 2)
    except ValueError:
        return False
    if scheme != "pbkdf2":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), PBKDF2_ROUNDS
    )
    return hmac.compare_digest(digest.hex(), hash_hex)


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
