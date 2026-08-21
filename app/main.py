from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.config import get_settings
from app.csrf import CSRFMiddleware
from app.db import SessionLocal, init_db
from app.diagnostics import clear_pid_file, sync_log_level, write_pid_file
from app.logging_setup import setup_logging
from app.poller import start_poller, stop_poller
from app.prereq import check_prerequisites
from app.routers import admin, api, pages
from app.security_headers import SecurityHeadersMiddleware
from app.seed import ensure_hardened_users, seed

log = logging.getLogger("switcheroo")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    check_prerequisites(settings)
    setup_logging(settings)
    sync_log_level()
    init_db()
    db = SessionLocal()
    try:
        result = seed(db)
        ensure_hardened_users(db)
        log.info("Seed complete (new users=%s new switches=%s)", result["users"], result["switches"])
    finally:
        db.close()
    if not settings.testing:
        write_pid_file()
        start_poller()
    log.info(
        "Switcheroo ready driver=%s bind=%s:%s data_dir=%s (lab defaults are not production)",
        settings.driver,
        settings.host,
        settings.port,
        settings.data_dir,
    )
    yield
    if not settings.testing:
        stop_poller()
        clear_pid_file()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="Switcheroo", lifespan=lifespan, docs_url=None, redoc_url=None)
    application.add_middleware(CSRFMiddleware)
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=settings.cookie_secure,
        max_age=settings.session_max_age,
    )
    application.add_middleware(SecurityHeadersMiddleware)
    if settings.trusted_hosts_enabled:
        application.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(pages.router)
    application.include_router(api.router)
    application.include_router(admin.router)

    @application.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "app": "switcheroo",
            "version": __version__,
            "driver": settings.driver,
            "testing": settings.testing,
        }

    return application


app = create_app()
