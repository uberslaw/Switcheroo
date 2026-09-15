from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import get_settings

_SKIP_PREFIXES = ("/static",)
_SKIP_PATHS = {"/health", "/ready"}


class WindowsAuthMiddleware(BaseHTTPMiddleware):
    """Establish or refresh a session from a trusted IIS Windows identity header."""

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        path = request.url.path
        if (
            not settings.windows_auth
            or path in _SKIP_PATHS
            or path.startswith(_SKIP_PREFIXES)
        ):
            return await call_next(request)
        from app.auth_windows import apply_windows_identity
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            apply_windows_identity(db, request)
        finally:
            db.close()
        return await call_next(request)
