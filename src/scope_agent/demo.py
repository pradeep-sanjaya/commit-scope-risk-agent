"""Demo data for the QA portal: `scope-agent seed-demo`.

Writes impact records, findings and versioned Epic reports straight to the database, with no Bitbucket,
JIRA or LLM calls and no queued jobs. Every key uses the DEMO project and every repo the DEMO/ prefix, so
the data cannot be confused with (or shadow) real tickets. Ticket titles are put in the JIRA resolver's
cache without expiry, so the portal never calls JIRA for them. Demo findings are dated 2000-01-01, so they
never appear in a digest. Intended for local and demo databases.
"""

from datetime import datetime, timedelta, timezone

from scope_agent.domain.epic_report import ReportContent, aggregate, diff_reports
from scope_agent.domain.impact import Endpoint, ImpactRecord
from scope_agent.domain.models import DependencyChange, EpicTicket, Finding, FindingType, Severity, TicketContext
from scope_agent.llm.schemas import TestScopeItem
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.persistence.database import Database
from scope_agent.persistence.findings import FindingRepository
from scope_agent.persistence.impact import ImpactRepository
from scope_agent.persistence.reports import ReportRepository
from scope_agent.services.ticket_resolver import NAMESPACE as JIRA_CACHE_NAMESPACE

DEMO_JOB_ID = 0
FINDINGS_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)
PAYMENTS = "DEMO/payments-service"
GATEWAY = "DEMO/api-gateway"

# key -> (summary, issue type, epic key, assignee)
TICKETS: dict[str, tuple[str, str, str | None, str | None]] = {
    "DEMO-100": ("Feature flag platform migration", "Epic", None, None),
    "DEMO-101": ("Move flag reads to the OpenFeature flagd provider", "Story", "DEMO-100", "Asha Perera"),
    "DEMO-102": ("Checkout reads the new-flow flag from the flag client", "Story", "DEMO-100", "Nimal Silva"),
    "DEMO-103": ("Default flag values served by the gateway", "Story", "DEMO-100", "Ravi Kumar"),
    "DEMO-104": ("Remove the legacy LaunchDarkly SDK", "Story", "DEMO-100", None),
    "DEMO-200": ("Checkout redesign", "Epic", None, None),
    "DEMO-201": ("New payment method picker", "Story", "DEMO-200", "Nimal Silva"),
    "DEMO-202": ("Persist the selected payment method", "Story", "DEMO-200", "Kim Lee"),
    "DEMO-300": ("Search relevance tuning", "Epic", None, None),
    "DEMO-301": ("Boost exact SKU matches", "Story", "DEMO-300", "Kim Lee"),
    "DEMO-7": ("Fix typo in the receipt email", "Bug", None, "Asha Perera"),
}
AC = {
    "DEMO-101": "- Flags load from flagd on startup\n- A flagd outage falls back to the last known values",
    "DEMO-102": "- Checkout shows the new flow when checkout.newFlow is on\n- The old flow is unchanged when off",
    "DEMO-103": "- The gateway serves defaults when the flag service is unreachable",
    "DEMO-201": "- Customers can pick card, wallet or bank transfer",
}


def _context(key: str) -> TicketContext:
    summary, issue_type, epic, _ = TICKETS[key]
    return TicketContext(
        key=key, summary=summary, description=f"Demo ticket: {summary}.", issue_type=issue_type,
        status="In QA" if issue_type != "Epic" else "In Progress", acceptance_criteria=AC.get(key),
        components=[], labels=["demo"], epic_key=epic, epic_summary=TICKETS[epic][0] if epic else None,
        epic_description=None, linked_titles=[],
    )


def _epic_tickets(epic: str) -> list[EpicTicket]:
    return [
        EpicTicket(key=k, summary=s, status="In QA", assignee=a)
        for k, (s, _, e, a) in TICKETS.items() if e == epic
    ]


def _record(now: datetime, commit: str, keys: list[str], author: str, areas: list[str], *, hours_ago: float,
            repo: str = PAYMENTS, subject: str | None = None, severity: str | None = None,
            findings: tuple[str, ...] = (), notes: tuple[str, ...] = (), **extra) -> ImpactRecord:
    epic = TICKETS[keys[0]][2] if keys else None
    subject = subject or epic or (keys[0] if keys else f"unassigned/{repo}")
    return ImpactRecord(
        repo=repo, commit=commit, author_name=author,
        author_email=f"{author.split()[0].lower()}@demo.invalid",
        committed_at=now - timedelta(hours=hours_ago), jira_keys=keys, epic_key=epic, subject=subject,
        areas=areas, endpoints=extra.get("endpoints", []), migrations=extra.get("migrations", []),
        config_files=extra.get("config_files", []), dependency_changes=extra.get("dependency_changes", []),
        finding_ids=list(findings), max_severity=Severity(severity) if severity else None, test_note=list(notes),
    )


def _records(now: datetime) -> list[ImpactRecord]:
    return [
        # DEMO-100: two versions, an overlap on "Feature flags", findings of every severity.
        _record(now, "a1b2c3d4e5f6", ["DEMO-101"], "Asha Perera", ["Feature flags", "Gateway / Envoy"],
                hours_ago=50, severity="high", findings=("demo-f1",),
                notes=("Restart the service with flagd stopped; flags must fall back to cached values",),
                config_files=["deploy/envoy/flags.yaml"],
                dependency_changes=[
                    DependencyChange(ecosystem="maven", name="dev.openfeature:sdk", old_version=None,
                                     new_version="1.9.0", kind="added", file="pom.xml"),
                    DependencyChange(ecosystem="maven", name="com.launchdarkly:launchdarkly-java-server-sdk",
                                     old_version="6.3.0", new_version=None, kind="removed", file="pom.xml"),
                ]),
        _record(now, "9f8e7d6c5b4a", ["DEMO-101"], "Asha Perera", ["Feature flags"], hours_ago=40,
                severity="medium", notes=("Check GET /api/flags/{key} for an unknown key",),
                endpoints=[Endpoint("GET", "/api/flags/{key}", "added", "src/main/java/FlagController.java")]),
        _record(now, "b1c2d3e4f5a6", ["DEMO-103"], "Ravi Kumar", ["Feature flags", "Gateway / Envoy"],
                repo=GATEWAY, hours_ago=20, severity="medium", findings=("demo-f2",),
                notes=("Gateway defaults with the flag service down",),
                config_files=["helm/values.yaml"]),
        _record(now, "c1d2e3f4a5b6", ["DEMO-102"], "Nimal Silva", ["Checkout API"], hours_ago=6,
                severity="low", findings=("demo-f3",), notes=("Checkout with checkout.newFlow on and off",),
                endpoints=[Endpoint("POST", "/api/checkout", "changed", "src/main/java/CheckoutController.java")],
                migrations=["src/main/resources/db/migration/V42__add_flag_audit.sql"]),
        _record(now, "c7d8e9f0a1b2", ["DEMO-102"], "Nimal Silva", ["Checkout API", "Feature flags"],
                hours_ago=2, notes=("Flag audit rows written on checkout",)),
        # DEMO-200: one version whose test scope is still waiting for LLM budget.
        _record(now, "d1e2f3a4b5c6", ["DEMO-201"], "Nimal Silva", ["Checkout UI"], hours_ago=30,
                notes=("Each payment method selectable on mobile",)),
        _record(now, "d7e8f9a0b1c2", ["DEMO-202"], "Kim Lee", ["Checkout UI", "Checkout API"], hours_ago=12,
                severity="medium", findings=("demo-f4",)),
        # DEMO-300: commits recorded, report not built yet.
        _record(now, "e1f2a3b4c5d6", ["DEMO-301"], "Kim Lee", ["Search"], hours_ago=1, severity="low"),
        # A ticket without an Epic, and a commit without a ticket.
        _record(now, "f1a2b3c4d5e6", ["DEMO-7"], "Asha Perera", ["Notifications"], hours_ago=26),
        _record(now, "0a1b2c3d4e5f", [], "Build Bot", ["infra-scripts: docker"], repo="DEMO/infra-scripts",
                hours_ago=72),
    ]


def _finding(fid, repo, commit, key, ftype, category, severity, title, explanation, files) -> Finding:
    return Finding(
        id=fid, repo=repo, commit=commit, author_email=None, jira_keys=[key], type=ftype, category=category,
        severity=Severity(severity), title=title, explanation=explanation, evidence=[], files=files,
        suggested_reviewers=[], created_at=FINDINGS_AT,
    )


FINDINGS = [
    _finding("demo-f1", PAYMENTS, "a1b2c3d4e5f6", "DEMO-101", FindingType.dependency_behavior_change, "swap",
             "high", "Flag SDK swapped: LaunchDarkly removed, OpenFeature flagd added",
             "The new provider evaluates flags remotely; on a flagd outage it serves defaults, not cached values.",
             ["pom.xml"]),
    _finding("demo-f2", GATEWAY, "b1c2d3e4f5a6", "DEMO-103", FindingType.risky_change, "feature_flag_defaults",
             "medium", "Default for checkout.newFlow changed from false to true",
             "Every environment without an explicit override now gets the new checkout flow.",
             ["helm/values.yaml"]),
    _finding("demo-f3", PAYMENTS, "c1d2e3f4a5b6", "DEMO-102", FindingType.risky_change, "schema_migration",
             "low", "New table flag_audit (V42)", "Additive migration; no backfill.",
             ["src/main/resources/db/migration/V42__add_flag_audit.sql"]),
    _finding("demo-f4", PAYMENTS, "d7e8f9a0b1c2", "DEMO-202", FindingType.ticket_gap, "vague_ac",
             "medium", "Acceptance criteria missing for DEMO-202",
             "The ticket has no acceptance criteria, so QA cannot tell what 'persist' must cover.", []),
]


def _item(kind, what, why, *tickets) -> TestScopeItem:
    return TestScopeItem(type=kind, what=what, why=why, tickets=list(tickets))


SCOPE_V1 = [
    _item("functional", "Flags load from flagd on startup", "Acceptance criteria of DEMO-101", "DEMO-101"),
    _item("non_functional", "Restart with flagd unavailable", "Provider swap changes outage behaviour",
          "DEMO-101"),
]
SCOPE_V2 = SCOPE_V1 + [
    _item("integration", "Gateway defaults and checkout flag together",
          "DEMO-102 and DEMO-103 both change the Feature flags area", "DEMO-102", "DEMO-103"),
    _item("data", "flag_audit rows written for each checkout", "Migration V42", "DEMO-102"),
    _item("regression", "Checkout with checkout.newFlow off", "Default flipped to true", "DEMO-102", "DEMO-103"),
]


def seed_demo(db: Database, now: datetime) -> bool:
    """Seed the demo data. Returns False (and changes nothing) when it is already present."""
    reports_v1 = ReportRepository(db, lambda: now - timedelta(hours=39))
    if reports_v1.latest("DEMO-100") is not None:
        return False
    cache = KeyValueCache(db)
    for key in TICKETS:
        cache.put(JIRA_CACHE_NAMESPACE, key, _context(key).model_dump_json(), None)
    records = _records(now)
    ImpactRepository(db).save(records)
    FindingRepository(db).save(DEMO_JOB_ID, FINDINGS, {}, [])

    def of(subject: str) -> list[ImpactRecord]:
        return [r for r in records if r.subject == subject]

    flags, flag_tickets = of("DEMO-100"), _epic_tickets("DEMO-100")
    v1 = ReportContent(aggregate=aggregate("DEMO-100", flags[:2], flag_tickets), test_scope=SCOPE_V1,
                       test_scope_status="ok")
    reports_v1.append("DEMO-100", v1, diff_reports(None, v1), 2)
    v2 = ReportContent(aggregate=aggregate("DEMO-100", flags, flag_tickets), test_scope=SCOPE_V2,
                       test_scope_status="ok")
    ReportRepository(db, lambda: now - timedelta(hours=1)).append("DEMO-100", v2, diff_reports(v1, v2), len(flags))

    checkout = of("DEMO-200")
    pending = ReportContent(aggregate=aggregate("DEMO-200", checkout, _epic_tickets("DEMO-200")),
                            test_scope=None, test_scope_status="pending")
    ReportRepository(db, lambda: now - timedelta(hours=11)).append(
        "DEMO-200", pending, diff_reports(None, pending), len(checkout))
    return True
