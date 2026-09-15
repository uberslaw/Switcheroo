from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

_SKIP_PREFIXES = ("/static",)
_SKIP_PATHS = {"/health", "/ready"}


class IdleSessionMiddleware(BaseHTTPMiddleware):
    """Expire a signed-in session after SWITCHEROO_SESSION_IDLE_SECONDS of no requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        idle = get_settings().session_idle_seconds
        path = request.url.path
        if idle <= 0 or path in _SKIP_PATHS or path.startswith(_SKIP_PREFIXES):
            return await call_next(request)
        if request.session.get("user_id"):
            now = time.time()
            last = request.session.get("last_seen")
            if isinstance(last, (int, float)) and (now - last) > idle:
                request.session.clear()
            else:
                request.session["last_seen"] = now
        return await call_next(request)
