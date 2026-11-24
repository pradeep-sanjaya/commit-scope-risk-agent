from datetime import datetime, timedelta, timezone

import pytest

from scope_agent.domain.models import Finding, FindingType, Severity
from scope_agent.persistence.database import Database


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    return d


@pytest.fixture
def finding():
    return Finding(
        repo="PAY/r", commit="abc", author_email="a@x.com", jira_keys=["PAY-1"],
        type=FindingType.risky_change, category="auth", severity=Severity.high,
        title="t", explanation="e", evidence=["x"], files=["f.py"], suggested_reviewers=["bob"],
    )
