from __future__ import annotations

import hmac
import secrets
from urllib.parse import parse_qs

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app.config import get_settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 16:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


class CSRFMiddleware(BaseHTTPMiddleware):
    """Require a session CSRF token on cookie-authenticated state changes.

    Tests keep this off (SWITCHEROO_TESTING=1) so existing clients stay simple.
    Browsers get the token from a meta tag / hidden field; HTMX sends X-CSRF-Token.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)
        if not get_settings().csrf_enabled:
            return await call_next(request)
        path = request.url.path
        if path.startswith("/static") or path == "/health":
            return await call_next(request)
        expected = request.session.get("csrf_token")
        submitted = (request.headers.get("x-csrf-token") or request.headers.get("x-csrftoken") or "").strip()
        if not submitted:
            submitted = await _form_csrf_token(request)
        if not isinstance(expected, str) or not submitted or not hmac.compare_digest(expected, submitted):
            return PlainTextResponse("CSRF token missing or invalid.", status_code=403)
        return await call_next(request)


async def _form_csrf_token(request: Request) -> str:
    """Read csrf_token without preventing FastAPI from parsing the same body."""
    content_type = (request.headers.get("content-type") or "").lower()
    raw = await request.body()
    if "application/x-www-form-urlencoded" in content_type:
        values = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        items = values.get("csrf_token") or []
        return items[0] if items else ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        raw_token = form.get("csrf_token")
        return raw_token if isinstance(raw_token, str) else ""
    return ""
