"""Orchestrates the analysis of one push; business rules live in the domain modules."""

from dataclasses import dataclass, field

from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.domain.dependency_files import dependency_changes
from scope_agent.domain.jira_keys import extract_keys
from scope_agent.domain.models import (
    CommitData, DependencyChange, Finding, LibrarySummary, PushJob, TicketContext,
)
from scope_agent.domain.ticket_gaps import ticket_gaps
from scope_agent.persistence.commits import CommitLedger
from scope_agent.persistence.database import Database
from scope_agent.persistence.findings import FindingRepository
from scope_agent.services.code_analysis import CodeAnalyzer
from scope_agent.services.dependency_analysis import DependencyAnalyzer
from scope_agent.services.ticket_resolver import TicketResolver


@dataclass
class PushResult:
    job_id: int
    job: PushJob
    commits: list[CommitData] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    summaries: dict[str, list[LibrarySummary]] = field(default_factory=dict)
    keys_by_commit: dict[str, list[str]] = field(default_factory=dict)
    tickets: dict[str, TicketContext | None] = field(default_factory=dict)
    dependency_changes: dict[str, list[DependencyChange]] = field(default_factory=dict)
    test_notes: dict[str, list[str]] = field(default_factory=dict)


class PushPipeline:
    def __init__(
        self,
        bitbucket: BitbucketClient,
        tickets: TicketResolver,
        deps: DependencyAnalyzer,
        code: CodeAnalyzer,
        db: Database,
        findings: FindingRepository,
        ledger: CommitLedger,
        project_keys: list[str],
        max_commits: int,
    ):
        self._bitbucket = bitbucket
        self._tickets = tickets
        self._deps = deps
        self._code = code
        self._db = db
        self._findings = findings
        self._ledger = ledger
        self._project_keys = project_keys
        self._max_commits = max_commits

    def run(self, job_id: int, job: PushJob, force: bool = False) -> PushResult:
        repo = job.repo
        commits, merges = self._load_commits(job, force)
        result = PushResult(job_id=job_id, job=job, commits=commits)
        if not commits:
            self._ledger.mark_analyzed(repo, merges)
            return result

        for c in commits:
            keys = extract_keys(c.message, job.branch, self._project_keys)
            result.keys_by_commit[c.sha] = keys
            for key in keys:
                if key not in result.tickets:
                    result.tickets[key] = self._tickets.resolve(key)

        result.dependency_changes = {
            c.sha: [d for fc in c.files for d in dependency_changes(fc)] for c in commits
        }
        errors: list[tuple[str, str | None, str]] = []
        dep = self._deps.analyze(commits, result.dependency_changes)
        result.findings.extend(dep.findings)
        result.summaries = dep.summaries
        errors.extend((repo, None, m) for m in dep.errors)

        reported: set[tuple[str, str]] = set()
        for c in commits:
            keys = result.keys_by_commit[c.sha]
            resolved = [t for k in keys if (t := result.tickets.get(k)) is not None]
            analysis = self._code.analyze(c, resolved)
            result.findings.extend(analysis.findings)
            result.test_notes[c.sha] = list(analysis.test_note)
            errors.extend((repo, c.sha, m) for m in analysis.errors)
            quality = analysis.ac_quality
            if quality == "missing" and any(t.acceptance_criteria for t in resolved):
                quality = None  # trust the ticket field over the model
            result.findings.extend(ticket_gaps(c, keys, result.tickets, quality, reported))

        by_sha = {c.sha: c for c in commits}
        for f in result.findings:
            f.repo = repo
            f.jira_keys = list(result.keys_by_commit.get(f.commit, []))
            if f.commit in by_sha:
                f.author_email = by_sha[f.commit].author_email

        with self._db.session() as s:
            self._findings.save(job_id, result.findings, result.summaries, errors, session=s)
            self._ledger.mark_analyzed(repo, [c.sha for c in commits] + merges, session=s)
        return result

    def _load_commits(self, job: PushJob, force: bool) -> tuple[list[CommitData], list[str]]:
        commits: list[CommitData] = []
        merges: list[str] = []
        for sha in self._bitbucket.commits_in_push(job, self._max_commits):
            if not force and self._ledger.is_analyzed(job.repo, sha):
                continue
            commit = self._bitbucket.get_commit(job.repo, sha)
            (merges if commit.is_merge else commits).append(commit.sha if commit.is_merge else commit)
        return commits, merges
