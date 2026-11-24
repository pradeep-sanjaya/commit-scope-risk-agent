import hashlib
import hmac
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scope_agent.persistence.database import Database
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web.app import create_app

SECRET = "whsec"
BODY = (Path(__file__).parent.parent / "fixtures" / "refs_changed.json").read_bytes()


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def parts():
    db = Database("sqlite://")
    db.create_all()
    return JobQueue(db), RatingRepository(db), db


@pytest.fixture
def client(parts):
    jobs, ratings, _ = parts
    app = create_app(SECRET, ["development", "hotfix/*", "release/*"], jobs, ratings, "rsecret")
    return TestClient(app)


def post(client, body=BODY, event="repo:refs_changed", sig=None):
    headers = {"X-Event-Key": event, "X-Hub-Signature": sig if sig is not None else sign(body)}
    return client.post("/webhook/bitbucket", content=body, headers=headers)


def test_webhook_bad_signature_401(client):
    assert post(client, sig="sha256=deadbeef").status_code == 401


def test_webhook_non_ascii_signature_401(client):
    r = client.post("/webhook/bitbucket", content=BODY,
                    headers={"X-Event-Key": "repo:refs_changed",
                             "X-Hub-Signature": "sha256=é".encode("utf-8")})
    assert r.status_code == 401


def test_webhook_ping_200(client):
    assert post(client, b"{}", event="diagnostics:ping").status_code == 200


def test_webhook_enqueue_and_redelivery_dedup(client, parts):
    first = post(client)
    assert (first.status_code, first.json()) == (202, {"queued": 2})
    again = post(client)
    assert (again.status_code, again.json()) == (202, {"queued": 0})
    assert parts[0].count_by_status() == {"queued": 2}


def test_webhook_other_event_204(client):
    assert post(client, b"{}", event="pr:opened").status_code == 204


def test_webhook_invalid_json_400(client):
    body = b"not json"
    assert post(client, body).status_code == 400


def test_healthz(client):
    assert client.get("/healthz").status_code == 200
