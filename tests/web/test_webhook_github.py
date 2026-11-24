import json

import pytest
from fastapi.testclient import TestClient

from scope_agent.persistence.database import Database
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web.app import create_app
from tests.web.test_webhook import SECRET, sign

PUSH = json.dumps({
    "ref": "refs/heads/main", "before": "a" * 40, "after": "b" * 40, "created": False, "deleted": False,
    "repository": {"full_name": "acme/payments"}, "pusher": {"name": "jdoe", "email": "jdoe@example.com"},
}).encode()


@pytest.fixture
def jobs():
    db = Database("sqlite://")
    db.create_all()
    return JobQueue(db)


def app(jobs, provider):
    db_ratings = RatingRepository(jobs._db)
    return TestClient(create_app(SECRET, ["main"], jobs, db_ratings, "r", source_provider=provider))


def post(client, body=PUSH, event="push", sig=None):
    headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": sig if sig is not None else sign(body),
               "Content-Type": "application/json"}
    return client.post("/webhook/github", content=body, headers=headers)


def test_github_push_enqueues_once_and_redelivery_dedups(jobs):
    c = app(jobs, "github")
    first = post(c)
    assert (first.status_code, first.json()) == (202, {"queued": 1})
    assert post(c).json() == {"queued": 0}
    job = jobs.claim(60)
    assert job.payload["repo"] == "acme/payments" and job.payload["branch"] == "main"
    assert job.payload["from_hash"] == "a" * 40 and job.payload["pushed_by"] == "jdoe@example.com"


def test_github_signature_ping_and_other_events(jobs):
    c = app(jobs, "github")
    assert post(c, sig="sha256=bad").status_code == 401
    assert post(c, sig="").status_code == 401
    assert post(c, body=b'{"zen": "hi"}', event="ping").status_code == 200
    assert post(c, body=b"{}", event="pull_request").status_code == 204
    assert post(c, body=b"not json").status_code == 400
    assert post(c, body=b'{"ref": "refs/heads/main"}').status_code == 400  # missing fields
    assert jobs.count_by_status() == {}


def test_only_the_configured_providers_webhook_is_served(jobs):
    github = app(jobs, "github")
    assert github.post("/webhook/bitbucket", content=b"{}").status_code == 404
    bitbucket = app(jobs, "bitbucket")
    assert post(bitbucket).status_code == 404
