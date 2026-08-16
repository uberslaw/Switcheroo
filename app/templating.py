from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.models import utcnow
from app.services.uptime import format_connected_for, short_if_name

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S") + " UTC"


def _ago(value: datetime | None) -> str:
    if value is None:
        return "never"
    delta = utcnow() - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _purpose_label(value: str) -> str:
    return (value or "").replace("_", " ")


templates.env.filters["dt"] = _fmt_dt
templates.env.filters["ago"] = _ago
templates.env.filters["purpose"] = _purpose_label
templates.env.filters["connected"] = format_connected_for
templates.env.filters["giface"] = short_if_name


def render(request: Request, name: str, status_code: int = 200, **context):
    settings = get_settings()
    context.setdefault("request", request)
    context.setdefault("settings", settings)
    context.setdefault("flashes", request.session.pop("flashes", []))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def flash(request: Request, message: str, category: str = "info") -> None:
    flashes = request.session.get("flashes", [])
    flashes.append({"message": message, "category": category})
    request.session["flashes"] = flashes
