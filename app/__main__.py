from __future__ import annotations

import uvicorn

from app.config import get_settings
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
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
