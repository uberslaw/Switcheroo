from __future__ import annotations

import logging

import uvicorn

from app.config import get_settings
from app.diagnostics import desired_log_level
from app.prereq import check_prerequisites


def main() -> None:
    settings = get_settings()
    check_prerequisites(settings)
    if settings.bind_is_all_interfaces:
        print(
            "WARNING: SWITCHEROO_HOST binds all interfaces. This is an internal tool. "
            "Restrict with a host firewall or reverse proxy. Do not expose to the internet."
        )
    print(f"Switcheroo listening on http://{settings.host}:{settings.port}")
    print(f"Logs: {settings.log_file}")
    if settings.show_lab_credentials:
        print("Lab logins: networks / networks  and  cs / cs  (not for production)")
    uv_level = logging.getLevelName(desired_log_level()).lower()
    if uv_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        uv_level = "info"
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=uv_level,
    )


if __name__ == "__main__":
    main()
