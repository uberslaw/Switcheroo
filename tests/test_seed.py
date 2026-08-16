from __future__ import annotations

from sqlalchemy import func, select

from app.models import Port, Switch, User
from app.seed import seed


def test_seed_is_idempotent(db):
    first = seed(db)
    assert first["users"] == 2
    assert first["switches"] == 2
    users = db.scalar(select(func.count(User.id)))
    switches = db.scalar(select(func.count(Switch.id)))
    ports = db.scalar(select(func.count(Port.id)))
    assert users == 2
    assert switches == 2
    assert ports == 96

    second = seed(db)
    assert second["users"] == 0
    assert second["switches"] == 0
    assert db.scalar(select(func.count(User.id))) == 2
    assert db.scalar(select(func.count(Switch.id))) == 2
    assert db.scalar(select(func.count(Port.id))) == 96
    names = set(db.scalars(select(Switch.name)).all())
    assert names == {"CS-BLD-A-AS01", "CS-BLD-B-AS01"}
