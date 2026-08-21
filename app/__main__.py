from __future__ import annotations

import logging
import signal
import sys

import uvicorn

from app.config import get_settings
from app.diagnostics import desired_log_level
from app.prereq import check_prerequisites


def _install_stop_handlers(server: uvicorn.Server) -> None:
    def request_exit(*_args) -> None:
        server.should_exit = True

    signal.signal(signal.SIGINT, request_exit)
    sigbreak = getattr(signal, "SIGBREAK", None)
    if sigbreak is not None:
        signal.signal(sigbreak, request_exit)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is not None:
        try:
            signal.signal(sigterm, request_exit)
        except (OSError, ValueError, RuntimeError):
            pass

    if sys.platform != "win32":
        return
    try:
        import ctypes

        HandlerRoutine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)

        def _ctrl_handler(ctrl_type: int) -> int:
            # 0 CTRL_C, 1 CTRL_BREAK, 2 CLOSE
            if ctrl_type in (0, 1, 2):
                request_exit()
                return 1
            return 0

        cb = HandlerRoutine(_ctrl_handler)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(cb, True)
        server._switcheroo_ctrl_handler = cb  # keep ref for the process lifetime
    except Exception:
        pass


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
    print("Lab logins: networks / networks  and  cs / cs  (not for production)")
    uv_level = logging.getLevelName(desired_log_level()).lower()
    if uv_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        uv_level = "info"
    config = uvicorn.Config(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=uv_level,
        timeout_graceful_shutdown=8,
    )
    server = uvicorn.Server(config)
    _install_stop_handlers(server)
    server.run()


if __name__ == "__main__":
    main()
