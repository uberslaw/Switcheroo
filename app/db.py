from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        future=True,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()


def _migrate_sqlite() -> None:
    """Add columns create_all will not attach to an existing SQLite file."""
    if not get_settings().database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(ports)")).fetchall()
        names = {row[1] for row in rows}
        if names and "link_up_since" not in names:
            conn.execute(text("ALTER TABLE ports ADD COLUMN link_up_since DATETIME"))
        req_rows = conn.execute(text("PRAGMA table_info(change_requests)")).fetchall()
        req_names = {row[1] for row in req_rows}
        alters = {
            "from_vlan_id": "INTEGER",
            "from_vlan_name": "VARCHAR(64)",
            "servicenow_sys_id": "VARCHAR(64)",
            "servicenow_correlation_id": "VARCHAR(128)",
            "auto_approved": "INTEGER DEFAULT 0",
            "auto_approve_reason": "VARCHAR(256)",
            "acknowledged_by_id": "INTEGER",
            "acknowledged_at": "DATETIME",
        }
        if req_names:
            for col, typ in alters.items():
                if col not in req_names:
                    conn.execute(text(f"ALTER TABLE change_requests ADD COLUMN {col} {typ}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
