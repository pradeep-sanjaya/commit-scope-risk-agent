from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from scope_agent import cli
from scope_agent.demo import seed_demo
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.persistence.database import Database
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.services.portal_queries import NO_EPIC, UNASSIGNED, PortalQueries
from scope_agent.services.ticket_resolver import TicketResolver
from scope_agent.web.app import create_app
from tests.test_settings import set_env

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)


class NoJira:
    """The portal must answer from the seeded cache, never from JIRA."""

    def get_issue(self, *a, **k):
        raise AssertionError("JIRA called for demo data")

    def search(self, *a, **k):
        raise AssertionError("JIRA called for demo data")


@pytest.fixture
def seeded():
    db = Database("sqlite://")
    db.create_all()
    assert seed_demo(db, NOW) is True
    resolver = TicketResolver(NoJira(), KeyValueCache(db), None, "Acceptance Criteria", 600)
    q = PortalQueries(ReportRepository(db), ImpactRepository(db), FindingRepository(db), resolver)
    return db, q


def test_seed_populates_every_portal_state(seeded):
    db, q = seeded
    rows = q.epic_list(None, None, False)
    assert [r.subject for r in rows] == ["DEMO-300", "DEMO-100", "DEMO-200", NO_EPIC, UNASSIGNED]
    pending_report, flags, checkout = rows[0], rows[1], rows[2]
    assert pending_report.report_pending and pending_report.title == "Search relevance tuning"
    assert flags.title == "Feature flag platform migration"
    assert (flags.tickets, flags.developers, flags.overlaps) == (4, 3, 2)  # Feature flags, Gateway / Envoy
    assert flags.severity_counts["high"] == 1 and flags.new_commits_since_prev == 3
    page = q.epic_page("DEMO-100", None)
    assert [v.version for v in page.versions] == [1, 2]
    assert {i.type for i in page.report.content.test_scope} == {
        "functional", "non_functional", "integration", "data", "regression"}
    assert q.epic_page("DEMO-200", None).report.content.test_scope_status == "pending"
    ticket = q.ticket_page("DEMO-101")
    assert [r.commit for r in ticket.records] == ["9f8e7d6c5b4a", "a1b2c3d4e5f6"]
    assert [f.id for f in ticket.findings] == ["demo-f1"]
    assert [r.commit for r in q.matrix_cell("DEMO-100", "Feature flags", "DEMO-103")] == ["b1c2d3e4f5a6"]


def test_seed_is_idempotent(seeded):
    db, q = seeded
    assert seed_demo(db, NOW + timedelta(days=1)) is False
    assert [v.version for v in q.epic_page("DEMO-100", None).versions] == [1, 2]


def test_demo_findings_stay_out_of_the_digest(seeded):
    db, _ = seeded
    assert FindingRepository(db).since(NOW - timedelta(days=365), NOW + timedelta(days=1)) == []
    assert JobQueue(db).count_by_status() == {}  # nothing queued for the worker


def test_seeded_portal_pages_render(seeded):
    db, q = seeded
    c = TestClient(create_app("w", ["development"], JobQueue(db), RatingRepository(db), "r", portal_queries=q))
    home = c.get("/portal")
    assert home.status_code == 200 and "DEMO-100" in home.text and "Report pending" in home.text
    assert "Feature flags" in c.get("/portal/epics/DEMO-100").text
    assert "Test scope pending (LLM budget)" in c.get("/portal/epics/DEMO-200").text
    assert "non_functional" in c.get("/portal/epics/DEMO-100/tab/test-scope").text
    assert "a1b2c3d" in c.get("/portal/tickets/DEMO-101").text


def test_cli_seed_demo(monkeypatch, data_files):
    set_env(monkeypatch)
    db = Database("sqlite://")
    container = SimpleNamespace(db=db, settings=SimpleNamespace(public_base_url="http://localhost:8080/"))
    monkeypatch.setattr(cli, "build_container", lambda settings: container)
    runner = CliRunner()
    first = runner.invoke(cli.app, ["seed-demo"])
    assert first.exit_code == 0, first.output
    assert "demo_seeded" in first.output and "http://localhost:8080/portal" in first.output
    again = runner.invoke(cli.app, ["seed-demo"])
    assert again.exit_code == 0 and "demo_already_seeded" in again.output
