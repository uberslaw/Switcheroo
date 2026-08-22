from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.filesec import restrict_private_file, sqlite_filesystem_path


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
    db_path = sqlite_filesystem_path(get_settings().database_url)
    if db_path is not None:
        restrict_private_file(db_path)


def _migrate_sqlite() -> None:
    """Add columns create_all will not attach to an existing SQLite file."""
    if not get_settings().database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(ports)")).fetchall()
        names = {row[1] for row in rows}
        if names and "link_up_since" not in names:
            conn.execute(text("ALTER TABLE ports ADD COLUMN link_up_since DATETIME"))
        if names and "faulty" not in names:
            conn.execute(text("ALTER TABLE ports ADD COLUMN faulty INTEGER DEFAULT 0"))
        pp_rows = conn.execute(text("PRAGMA table_info(patch_panels)")).fetchall()
        pp_names = {row[1] for row in pp_rows}
        if pp_names:
            if "switch_id" not in pp_names:
                conn.execute(text("ALTER TABLE patch_panels ADD COLUMN switch_id INTEGER"))
            if "placement" not in pp_names:
                conn.execute(text("ALTER TABLE patch_panels ADD COLUMN placement VARCHAR(16) DEFAULT ''"))
        ppp_rows = conn.execute(text("PRAGMA table_info(patch_panel_ports)")).fetchall()
        ppp_names = {row[1] for row in ppp_rows}
        if ppp_names and "field_outlet_id" not in ppp_names:
            conn.execute(text("ALTER TABLE patch_panel_ports ADD COLUMN field_outlet_id INTEGER"))
        sw_rows = conn.execute(text("PRAGMA table_info(switches)")).fetchall()
        sw_names = {row[1] for row in sw_rows}
        switch_alters = {
            "room": "VARCHAR(128) DEFAULT ''",
            "stack_name": "VARCHAR(128) DEFAULT ''",
            "stack_role": "VARCHAR(32) DEFAULT ''",
            "member_number": "INTEGER DEFAULT 0",
            "rack_order": "INTEGER DEFAULT 0",
            "chassis_model": "VARCHAR(32) DEFAULT '9300'",
        }
        if sw_names:
            for col, typ in switch_alters.items():
                if col not in sw_names:
                    conn.execute(text(f"ALTER TABLE switches ADD COLUMN {col} {typ}"))
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
            "reason": "TEXT",
            "windows_account": "VARCHAR(256)",
            "sn_req_number": "VARCHAR(64)",
            "sn_ritm_number": "VARCHAR(64)",
            "sn_req_sys_id": "VARCHAR(64)",
            "sn_ritm_sys_id": "VARCHAR(64)",
        }
        if req_names:
            for col, typ in alters.items():
                if col not in req_names:
                    conn.execute(text(f"ALTER TABLE change_requests ADD COLUMN {col} {typ}"))
        sw_rows = conn.execute(text("PRAGMA table_info(switches)")).fetchall()
        sw_names = {row[1] for row in sw_rows}
        if sw_names and "monitoring_enabled" not in sw_names:
            conn.execute(text("ALTER TABLE switches ADD COLUMN monitoring_enabled INTEGER DEFAULT 1"))
        rack_rows = conn.execute(text("PRAGMA table_info(racks)")).fetchall()
        rack_names = {row[1] for row in rack_rows}
        rack_alters = {
            "rack_room_id": "INTEGER",
            "width_mm": "INTEGER DEFAULT 600",
            "depth_mm": "INTEGER DEFAULT 1000",
            "plinth_mm": "INTEGER DEFAULT 100",
            "roof_mm": "INTEGER DEFAULT 0",
            "pos_x_mm": "INTEGER DEFAULT 0",
            "pos_y_mm": "INTEGER DEFAULT 0",
            "rotation_deg": "INTEGER DEFAULT 0",
            "cable_entry": "VARCHAR(16) DEFAULT 'top'",
        }
        if rack_names:
            for col, typ in rack_alters.items():
                if col not in rack_names:
                    conn.execute(text(f"ALTER TABLE racks ADD COLUMN {col} {typ}"))
    _encrypt_legacy_switch_passwords()


def _encrypt_legacy_switch_passwords() -> None:
    """Rewrite plaintext TACACS/device passwords to enc:v1: blobs."""
    from sqlalchemy import select

    from app.crypto import PREFIX, store_secret
    from app.models import Switch

    db = SessionLocal()
    try:
        changed = 0
        for switch in db.scalars(select(Switch)).all():
            if switch.password and not switch.password.startswith(PREFIX):
                switch.password = store_secret(switch.password)
                changed += 1
        if changed:
            db.commit()
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
