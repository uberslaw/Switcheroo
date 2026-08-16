from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from app.auth import hash_password
from app.config import get_settings
from app.drivers.teams import _safe_filename, build_acknowledged_payload
from app.models import ROLE_NETWORKS, REQUEST_VLAN, User
from app.services.request_service import RequestError, acknowledge_request, create_request, release_acknowledgement
from tests.conftest import first_port


def _second_networks(db) -> User:
    user = User(
        username="networks2",
        password_hash=hash_password("networks2"),
        role=ROLE_NETWORKS,
        display_name="Networks Two",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_acknowledge_records_owner_and_teams_follow_up(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    acknowledge_request(seeded_db, req, nets)
    seeded_db.commit()
    assert req.acknowledged_by_id == nets.id
    assert req.acknowledged_at is not None
    folder = Path(get_settings().data_dir) / "teams-dryrun"
    data = json.loads((folder / _safe_filename(req.id)).read_text(encoding="utf-8"))
    assert data["last_update"]["action"] == "acknowledge"
    payload = data["last_update"]["payload"]
    card = payload["attachments"][0]["content"]
    assert card["body"][0]["text"] == f"VLAN request #{req.id} acknowledged"
    assert "handling this" in card["body"][1]["text"]
    assert "networks" in json.dumps(payload).lower()


def test_second_networks_user_cannot_double_claim(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    other = _second_networks(seeded_db)
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    acknowledge_request(seeded_db, req, nets)
    seeded_db.flush()
    try:
        acknowledge_request(seeded_db, req, other)
        raise AssertionError("second claim should fail")
    except RequestError as exc:
        assert "already acknowledged" in str(exc).lower()
    acknowledge_request(seeded_db, req, nets)
    assert req.acknowledged_by_id == nets.id


def test_release_clears_claim_and_posts_available(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    acknowledge_request(seeded_db, req, nets)
    release_acknowledgement(seeded_db, req, nets)
    seeded_db.commit()
    assert req.acknowledged_by_id is None
    assert req.acknowledged_at is None
    folder = Path(get_settings().data_dir) / "teams-dryrun"
    data = json.loads((folder / _safe_filename(req.id)).read_text(encoding="utf-8"))
    assert data["last_update"]["action"] == "release"
    card = data["last_update"]["payload"]["attachments"][0]["content"]
    assert "available again" in card["body"][0]["text"].lower()


def test_acknowledged_payload_names_handler(seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    nets = seeded_db.scalar(select(User).where(User.username == "networks"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.flush()
    payload = build_acknowledged_payload(req, nets)
    text = json.dumps(payload)
    assert "handling this" in text
    assert "networks" in text.lower()
    assert "no need to pick it up" in text.lower()


def test_unauthenticated_ack_redirects_to_login_next(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    response = client.get(f"/requests/{req.id}/ack", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.path == "/login"
    nxt = parse_qs(parsed.query).get("next", [""])[0]
    assert nxt == f"/requests/{req.id}/ack"
    login = client.post(
        "/login",
        data={"username": "networks", "password": "networks", "next": nxt},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert login.headers["location"] == f"/requests/{req.id}/ack"


def test_networks_can_acknowledge_from_page(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    client.post("/login", data={"username": "networks", "password": "networks"}, follow_redirects=False)
    page = client.get(f"/requests/{req.id}/ack")
    assert page.status_code == 200
    assert "I'm on it" in page.text
    posted = client.post(f"/requests/{req.id}/ack", follow_redirects=False)
    assert posted.status_code == 303
    seeded_db.refresh(req)
    assert req.acknowledged_by is not None
    assert req.acknowledged_by.username == "networks"
    shown = client.get("/requests?status=pending")
    assert "On it: networks" in shown.text
    assert "I'm on it" not in shown.text or "Release" in shown.text


def test_cs_cannot_acknowledge(client, seeded_db):
    port = first_port(seeded_db)
    cs = seeded_db.scalar(select(User).where(User.username == "cs"))
    req = create_request(seeded_db, cs, port, REQUEST_VLAN, vlan_id=50)
    seeded_db.commit()
    client.post("/login", data={"username": "cs", "password": "cs"}, follow_redirects=False)
    page = client.get(f"/requests/{req.id}/ack")
    assert page.status_code == 200
    assert "Waiting for Networks" in page.text
    posted = client.post(f"/requests/{req.id}/ack", follow_redirects=False)
    assert posted.status_code == 403
    seeded_db.refresh(req)
    assert req.acknowledged_by_id is None
