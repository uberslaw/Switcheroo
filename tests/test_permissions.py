from __future__ import annotations

from sqlalchemy import select

from app.models import Switch
from app.services.switch_service import PermissionDenied, get_switch_for_user, visible_switches
from tests.conftest import add_cs_user


def test_cs_user_only_sees_granted_switches(seeded_db, closed_access):
    limited = add_cs_user(seeded_db, "cs-limited", "cs-limited", ["CS-BLD-A-AS01"])
    names = [s.name for s in visible_switches(seeded_db, limited)]
    assert names == ["CS-BLD-A-AS01"]
    hidden = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    try:
        get_switch_for_user(seeded_db, limited, hidden.id)
        raise AssertionError("hidden switch should be denied")
    except PermissionDenied:
        pass


def test_open_access_shows_all_switches_to_limited_cs(seeded_db):
    limited = add_cs_user(seeded_db, "cs-limited", "cs-limited", ["CS-BLD-A-AS01"])
    names = {s.name for s in visible_switches(seeded_db, limited)}
    assert "CS-BLD-A-AS01" in names
    assert "CS-BLD-B-AS01" in names
    assert "BNE-L27-FS-01" in names
    hidden = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    assert get_switch_for_user(seeded_db, limited, hidden.id) is hidden


def test_cs_cannot_open_hidden_switch_page(client, seeded_db, closed_access):
    add_cs_user(seeded_db, "cs-limited", "cs-limited", ["CS-BLD-A-AS01"])
    hidden = seeded_db.scalar(select(Switch).where(Switch.name == "CS-BLD-B-AS01"))
    login = client.post("/login", data={"username": "cs-limited", "password": "cs-limited"}, follow_redirects=False)
    assert login.status_code == 303
    home = client.get("/")
    assert "CS-BLD-A-AS01" in home.text
    assert "CS-BLD-B-AS01" not in home.text
    detail = client.get(f"/switches/{hidden.id}", follow_redirects=False)
    assert detail.status_code == 303
    assert detail.headers["location"] == "/"
