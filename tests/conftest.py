from __future__ import annotations

import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="switcheroo-test-"))
os.environ["SWITCHEROO_TESTING"] = "1"
os.environ["SWITCHEROO_DATA_DIR"] = str(TMP)
os.environ["SWITCHEROO_DATABASE_URL"] = "sqlite:///" + (TMP / "test.db").as_posix()
os.environ["SWITCHEROO_LOG_FILE"] = str(TMP / "switcheroo.log")
os.environ["SWITCHEROO_SECRET_KEY"] = "test-secret-not-for-production"
os.environ["SWITCHEROO_DRIVER"] = "simulator"
os.environ["SWITCHEROO_SIM_FLAPS"] = "0"
os.environ["SWITCHEROO_STATUS_POLL_INTERVAL"] = "60"
os.environ["SWITCHEROO_ON_DEMAND_COOLDOWN"] = "60"
os.environ["SWITCHEROO_TROUBLESHOOT_DURATION"] = "300"
os.environ["SWITCHEROO_TROUBLESHOOT_INTERVAL"] = "10"
os.environ["SERVICENOW_ENABLED"] = "false"
os.environ["SERVICENOW_DRY_RUN"] = "true"
os.environ["SERVICENOW_INSTANCE"] = ""
os.environ["SERVICENOW_USERNAME"] = ""
os.environ["SERVICENOW_PASSWORD"] = ""

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

_REAL_HTTPX_REQUEST = httpx.Client.request


def _block_live_servicenow(self, method, url, *args, **kwargs):
    target = str(url)
    if "service-now.com" in target.lower():
        raise AssertionError(f"Blocked live ServiceNow HTTP in tests: {method} {target}")
    return _REAL_HTTPX_REQUEST(self, method, url, *args, **kwargs)


httpx.Client.request = _block_live_servicenow  # type: ignore[method-assign]

from app.auth import hash_password
from app.db import Base, SessionLocal, engine, init_db
from app.drivers.simulator import simulator
from app.main import app
from app.models import ROLE_CS, Port, Switch, User, UserSwitchPermission
from app.seed import seed


@pytest.fixture(autouse=True)
def _clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    simulator.reset()
    dry = TMP / "servicenow-dryrun"
    if dry.exists():
        for path in dry.glob("*.json"):
            path.unlink()
    yield
    simulator.reset()


@pytest.fixture
def db():
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded_db(db):
    seed(db)
    return db


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def cs_client(client):
    response = client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    assert response.status_code == 303
    return client


@pytest.fixture
def networks_client(client):
    response = client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    assert response.status_code == 303
    return client


def add_cs_user(db, username: str, password: str, switch_names: list[str] | None = None) -> User:
    user = User(username=username, password_hash=hash_password(password), role=ROLE_CS, display_name=username)
    db.add(user)
    db.flush()
    if switch_names:
        for name in switch_names:
            switch = db.scalar(select(Switch).where(Switch.name == name))
            if switch is not None:
                db.add(UserSwitchPermission(user_id=user.id, switch_id=switch.id))
    db.commit()
    return user


def first_port(db, switch_name: str = "CS-BLD-A-AS01") -> Port:
    switch = db.scalar(select(Switch).where(Switch.name == switch_name))
    assert switch is not None
    port = db.scalar(select(Port).where(Port.switch_id == switch.id).order_by(Port.if_index))
    assert port is not None
    simulator.hydrate_from_port(switch, port)
    return port
