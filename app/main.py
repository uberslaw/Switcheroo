from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.diagnostics import clear_pid_file, sync_log_level, write_pid_file
from app.logging_setup import setup_logging
from app.poller import start_poller, stop_poller
from app.prereq import check_prerequisites
from app.routers import admin, api, pages
from app.seed import seed

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
    application.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
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


@app.middleware("http")
async def no_cache_partials(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/partials") or request.headers.get("HX-Request"):
        response.headers["Cache-Control"] = "no-store"
    return response
