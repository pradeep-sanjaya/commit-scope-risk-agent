from datetime import datetime, timedelta

from scope_agent.adapters.smtp import EmailSender
from scope_agent.notify.digest_email import render_digest
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.persistence.database import Clock
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.meta import MetaStore

LAST_DIGEST_KEY = "last_digest_at"


class DigestService:
    def __init__(self, findings: FindingRepository, jobs: JobQueue, meta: MetaStore,
                 sender: EmailSender, links: LinkBuilder, recipients: list[str], clock: Clock):
        self._findings, self._jobs, self._meta = findings, jobs, meta
        self._sender, self._links = sender, links
        self._recipients, self._clock = recipients, clock

    def run(self) -> None:
        end = self._clock()
        last = self._meta.get(LAST_DIGEST_KEY)
        start = datetime.fromisoformat(last) if last else end - timedelta(hours=24)
        findings = self._findings.since(start, end)
        errors = self._findings.errors_between(start, end)
        deferred = self._jobs.count_by_status().get("deferred", 0)
        msg = render_digest(findings, errors, deferred, start, end, self._links)
        self._sender.send(msg, self._recipients)
        self._meta.set(LAST_DIGEST_KEY, end.isoformat())
