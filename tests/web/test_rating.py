from fastapi.testclient import TestClient

from scope_agent.notify.rating_links import rating_token
from scope_agent.persistence.database import Database, ratings
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web.app import create_app
from sqlalchemy import select


def make():
    db = Database("sqlite://")
    db.create_all()
    app = create_app("w", ["development"], JobQueue(db), RatingRepository(db), "rsecret")
    return TestClient(app), db


def stored(db):
    with db.session() as s:
        return [tuple(r) for r in s.execute(select(ratings.c.finding_id, ratings.c.value)).all()]


def test_rate_valid_and_invalid():
    client, db = make()
    t = rating_token("rsecret", "f1", "useful")
    ok = client.get("/rate/f1", params={"v": "useful", "t": t})
    assert ok.status_code == 200 and "Thanks, recorded." in ok.text
    assert stored(db) == [("f1", "useful")]
    assert client.get("/rate/f2", params={"v": "useful", "t": t}).status_code == 403
    assert client.get("/rate/f1", params={"v": "noise", "t": t}).status_code == 403
    assert client.get("/rate/f1", params={"v": "useful", "t": "é"}).status_code == 403
    assert stored(db) == [("f1", "useful")]


def test_rate_unknown_value_400():
    client, db = make()
    t = rating_token("rsecret", "f1", "meh")
    assert client.get("/rate/f1", params={"v": "meh", "t": t}).status_code == 400
    assert stored(db) == []
