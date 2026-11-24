"""Composition root: the only place concrete adapters, repositories and services are built."""

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy.engine import make_url

from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.adapters.github import GitHubClient
from scope_agent.adapters.http import build_http_client
from scope_agent.adapters.jira import JiraClient
from scope_agent.adapters.nexus import NexusClient
from scope_agent.adapters.smtp import EmailSender, SmtpSender
from scope_agent.adapters.web_pages import WebPageFetcher
from scope_agent.config_files import load_areas, load_dependency_hints, load_routing
from scope_agent.domain.areas import AreaMap
from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.routing import RoutingConfig
from scope_agent.llm.base import LLMClient
from scope_agent.llm.factory import build_llm
from scope_agent.notify.rating_links import LinkBuilder
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.persistence.commits import CommitLedger
from scope_agent.persistence.database import Database, utcnow
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.meta import MetaStore
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.persistence.usage import TokenUsageMeter
from scope_agent.services.code_analysis import CodeAnalyzer
from scope_agent.services.dependency_analysis import DependencyAnalyzer
from scope_agent.services.digest import DigestService
from scope_agent.services.impact_recorder import ImpactRecorder
from scope_agent.services.library_docs import LibraryDocsService
from scope_agent.services.library_profile import LibraryProfileBuilder
from scope_agent.services.pipeline import PushPipeline
from scope_agent.services.portal_queries import PortalQueries
from scope_agent.services.push_notifier import PushNotifier
from scope_agent.services.release_notes import ReleaseNotesService
from scope_agent.services.source_resolver import SourceResolver
from scope_agent.services.epic_report import EpicReportBuilder
from scope_agent.services.ticket_resolver import TicketResolver
from scope_agent.services.usage_context import UsageContextBuilder
from scope_agent.settings import Settings
from scope_agent.worker.handlers import EpicReportJobHandler, PushJobHandler
from scope_agent.worker.retry_policy import RetryPolicy
from scope_agent.worker.worker import Worker

log = structlog.get_logger()


@dataclass
class Container:
    settings: Settings
    routing: RoutingConfig
    hints: DependencyHints
    area_map: AreaMap
    db: Database
    jobs: JobQueue
    findings: FindingRepository
    impact: ImpactRepository
    ratings: RatingRepository
    ledger: CommitLedger
    meta: MetaStore
    sender: EmailSender
    links: LinkBuilder
    llm: LLMClient
    bitbucket: BitbucketClient
    pipeline: PushPipeline
    notifier: PushNotifier
    digest: DigestService
    worker: Worker
    portal: PortalQueries


def build_container(settings: Settings) -> Container:
    """Wire everything. Constructing clients performs no network I/O."""
    s = settings
    config = s.model_dump(mode="json")
    config["database_url"] = make_url(s.database_url).render_as_string(hide_password=True)
    log.info("startup_config", **config)
    routing = load_routing(s.routing_file)
    hints = load_dependency_hints(s.dependency_hints_file)
    area_map = load_areas(s.areas_file)

    db = Database(s.database_url)
    jobs, findings, ratings = JobQueue(db), FindingRepository(db), RatingRepository(db)
    impact = ImpactRepository(db)
    ledger, meta, cache = CommitLedger(db), MetaStore(db), KeyValueCache(db)
    llm = build_llm(s, TokenUsageMeter(db))

    timeout = s.http_timeout_seconds
    bitbucket = BitbucketClient(build_http_client(
        s.bitbucket_url, timeout,
        headers={"Authorization": f"Bearer {s.bitbucket_token.get_secret_value()}"},
    ))
    jira = JiraClient(build_http_client(
        s.jira_url, timeout, auth=httpx.BasicAuth(s.jira_email, s.jira_token.get_secret_value()),
    ))
    nexus = NexusClient(
        build_http_client(
            s.nexus_url, timeout, auth=httpx.BasicAuth(s.nexus_user, s.nexus_password.get_secret_value()),
            follow_redirects=True,
        ),
        s.nexus_maven_repo, s.nexus_npm_repo,
    )
    github_headers = (
        {"Authorization": f"Bearer {s.github_token.get_secret_value()}"} if s.github_token else None
    )
    github = GitHubClient(build_http_client(
        s.github_api_url, timeout, headers=github_headers, follow_redirects=True,
    ))
    pages = WebPageFetcher(build_http_client("", timeout, follow_redirects=True))
    sender = SmtpSender(
        s.smtp_host, s.smtp_port, s.smtp_user,
        s.smtp_password.get_secret_value() if s.smtp_password else None,
        s.smtp_starttls, s.smtp_from,
    )
    links = LinkBuilder(s.bitbucket_url, s.jira_url, s.public_base_url, s.rating_secret.get_secret_value())

    resolver = SourceResolver(nexus, hints, urlparse(s.bitbucket_url).hostname or "")
    notes = ReleaseNotesService(resolver, github, bitbucket, cache)
    docs = LibraryDocsService(resolver, github, bitbucket, pages, hints, s.docs_max_chars, cache)
    usage = UsageContextBuilder(bitbucket, s.usage_context_max_chars)
    profiles = LibraryProfileBuilder(resolver, bitbucket, s.library_profile_max_chars)
    deps = DependencyAnalyzer(llm, notes, docs, usage, profiles, hints)
    code = CodeAnalyzer(llm, s.files_exclude, s.files_priority, s.llm_max_input_tokens, s.max_chunks_per_commit)
    tickets = TicketResolver(jira, cache, s.jira_ac_field, s.jira_ac_heading, s.jira_cache_ttl_seconds)
    pipeline = PushPipeline(
        bitbucket, tickets, deps, code, db, findings, ledger, s.jira_project_keys, s.max_commits_per_push,
    )
    notifier = PushNotifier(routing, sender, links)
    recorder = ImpactRecorder(impact, jobs, area_map, s.files_priority, s.report_debounce_seconds, utcnow)
    report_repo = ReportRepository(db)
    reports = EpicReportBuilder(impact, tickets, findings, report_repo, llm, s.llm_max_input_tokens)
    worker = Worker(
        jobs,
        {"push": PushJobHandler(pipeline, notifier, recorder), "epic_report": EpicReportJobHandler(reports)},
        findings,
        RetryPolicy(s.retry_backoff_seconds), s.job_lease_seconds, s.timezone, utcnow,
    )
    digest = DigestService(findings, jobs, meta, sender, links, s.digest_recipients, utcnow)
    portal = PortalQueries(report_repo, impact, findings, tickets)
    return Container(
        settings=s, routing=routing, hints=hints, area_map=area_map, db=db, jobs=jobs, findings=findings,
        impact=impact, ratings=ratings,
        ledger=ledger, meta=meta, sender=sender, links=links, llm=llm, bitbucket=bitbucket,
        pipeline=pipeline, notifier=notifier, digest=digest, worker=worker, portal=portal,
    )
