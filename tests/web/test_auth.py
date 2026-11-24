import hashlib
import hmac
from pathlib import Path

from fastapi.testclient import TestClient

from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web.app import create_app
from tests.portal_helpers import seed

BODY = (Path(__file__).parent.parent / "fixtures" / "refs_changed.json").read_bytes()


def client(header):
    q, db, _ = seed()
    app = create_app("w", ["development"], JobQueue(db), RatingRepository(db), "rsecret",
                     portal_queries=q, portal_auth_header=header)
    return TestClient(app)


def test_auth_header_required_when_configured():
    c = client("X-Auth-User")
    assert c.get("/portal").status_code == 401
    assert c.get("/portal", headers={"X-Auth-User": ""}).status_code == 401
    assert c.get("/portal", headers={"X-Auth-User": "qa@x.io"}).status_code == 200
    assert c.get("/portal/epics/PAY-100").status_code == 401
    assert c.get("/healthz").status_code == 200
    body = BODY
    headers = {"X-Event-Key": "repo:refs_changed", "X-Hub-Signature": "sha256=" + hmac.new(b"w", body, hashlib.sha256).hexdigest()}
    assert c.post("/webhook/bitbucket", content=body, headers=headers).status_code == 202
    assert c.get("/rate/f1", params={"v": "useful", "t": "x"}).status_code == 403


def test_no_header_configured_is_open():
    assert client(None).get("/portal").status_code == 200
