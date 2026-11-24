from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from scope_agent.domain.areas import AreaMap
from scope_agent.domain.models import CommitData, PushJob, TicketContext
from scope_agent.persistence.database import Database, jobs as jobs_t
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.services.impact_recorder import ImpactRecorder
from scope_agent.services.pipeline import PushResult
from tests.persistence.conftest import FakeClock


@pytest.fixture
def db():
    d = Database("sqlite://")
    d.create_all()
    return d


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def jobs(db, clock):
    return JobQueue(db, clock)


def _commit(sha):
    return CommitData(repo="PAY/r", sha=sha, author_name="A", author_email=None, message="m", parents=["p"],
                      files=[], committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))


def _ticket(key, epic):
    return TicketContext(key=key, summary="s", description="d", issue_type="Task", status="x",
                         acceptance_criteria=None, components=[], labels=[], epic_key=epic,
                         epic_summary=None, epic_description=None, linked_titles=[])


def test_recorder_enqueues_one_debounced_report_job_per_subject(db, jobs, clock):
    repo = ImpactRepository(db)
    job = PushJob(repo="PAY/r", branch="development", from_hash=None, to_hash="c3", pushed_by=None)
    result = PushResult(
        job_id=1, job=job, commits=[_commit("c1"), _commit("c2"), _commit("c3")],
        keys_by_commit={"c1": ["PAY-101"], "c2": ["PAY-102"], "c3": []},
        tickets={"PAY-101": _ticket("PAY-101", "PAY-100"), "PAY-102": _ticket("PAY-102", "PAY-100")},
    )
    ImpactRecorder(repo, jobs, AreaMap(), [], 300, clock)(result)
    assert sorted(r.commit for r in repo.for_subject("PAY-100")) == ["c1", "c2"]
    assert [r.commit for r in repo.for_subject("unassigned/PAY/r")] == ["c3"]
    with db.session() as s:
        rows = s.execute(select(jobs_t)).all()
    assert len(rows) == 1
    assert rows[0].kind == "epic_report" and rows[0].payload == {"subject": "PAY-100"}
    assert rows[0].dedup_key == "epic_report:PAY-100"
    assert rows[0].not_before == clock.now + timedelta(seconds=300)
