from datetime import timedelta

from scope_agent.domain.areas import AreaMap
from scope_agent.domain.impact import build_impact_record
from scope_agent.persistence.database import Clock
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.services.pipeline import PushResult

UNASSIGNED_PREFIX = "unassigned/"


class ImpactRecorder:
    """after_push hook: record one impact per analyzed commit, then request debounced Epic reports."""

    def __init__(
        self,
        records: ImpactRepository,
        jobs: JobQueue,
        area_map: AreaMap,
        priority_globs: list[str],
        debounce_seconds: int,
        clock: Clock,
    ):
        self._records = records
        self._jobs = jobs
        self._area_map = area_map
        self._priority_globs = priority_globs
        self._debounce = timedelta(seconds=debounce_seconds)
        self._clock = clock

    def __call__(self, result: PushResult) -> None:
        records = [
            build_impact_record(
                c, result.keys_by_commit.get(c.sha, []), result.tickets,
                result.dependency_changes.get(c.sha, []), result.findings,
                result.test_notes.get(c.sha, []), self._area_map, self._priority_globs, c.committed_at,
            )
            for c in result.commits
        ]
        self._records.save(records)
        not_before = self._clock() + self._debounce
        for subject in dict.fromkeys(r.subject for r in records):
            if subject.startswith(UNASSIGNED_PREFIX):
                continue
            self._jobs.enqueue(
                "epic_report", {"subject": subject}, f"epic_report:{subject}", "pending", not_before=not_before,
            )
