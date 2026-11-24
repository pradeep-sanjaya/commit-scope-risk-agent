"""Epic impact report aggregation and diffing. Pure; no I/O."""

from typing import Literal

from pydantic import BaseModel

from scope_agent.domain.impact import ImpactRecord
from scope_agent.domain.models import EpicTicket, Severity
from scope_agent.llm.schemas import TestScopeItem


class AreaRow(BaseModel):
    area: str
    tickets: list[str]
    developers: list[str]
    commits: int
    change_kinds: list[str]
    max_severity: Severity | None
    overlap: bool


class MatrixCell(BaseModel):
    area: str
    ticket: str
    commits: int
    max_severity: Severity | None


class Aggregate(BaseModel):
    subject: str
    tickets: list[EpicTicket]
    area_rows: list[AreaRow]
    matrix: list[MatrixCell]
    overlaps: list[str]
    counts: dict[str, int]
    severity_counts: dict[str, int]


class ReportContent(BaseModel):
    aggregate: Aggregate
    test_scope: list[TestScopeItem] | None
    test_scope_status: Literal["ok", "pending", "error"]


class ReportDiff(BaseModel):
    new_commits: int = 0
    new_areas: list[str] = []
    new_overlaps: list[str] = []
    new_test_items: list[TestScopeItem] = []


def _rank(sev: Severity | None) -> int:
    return sev.rank if sev else -1


def _max(a: Severity | None, b: Severity | None) -> Severity | None:
    return a if _rank(a) >= _rank(b) else b


def developer(r: ImpactRecord) -> str:
    return r.author_email.lower() if r.author_email else r.author_name


def change_kinds(r: ImpactRecord) -> list[str]:
    kinds = []
    if r.endpoints:
        kinds.append("endpoint")
    if r.migrations:
        kinds.append("DB migration")
    if r.config_files:
        kinds.append("config")
    deps = r.dependency_changes
    if deps:
        swap = any(
            a.kind == "removed" and b.kind == "added" and a.ecosystem == b.ecosystem for a in deps for b in deps
        )
        kinds.append("library swap" if swap else "dependency")
    return kinds or ["code"]


def aggregate(subject: str, records: list[ImpactRecord], tickets: list[EpicTicket]) -> Aggregate:
    by_area: dict[str, list[ImpactRecord]] = {}
    for r in records:
        for area in dict.fromkeys(r.areas):
            by_area.setdefault(area, []).append(r)

    rows: list[AreaRow] = []
    for area, recs in by_area.items():
        sev: Severity | None = None
        kinds: dict[str, None] = {}
        for r in recs:
            sev = _max(sev, r.max_severity)
            kinds.update(dict.fromkeys(change_kinds(r)))
        keys = sorted({k for r in recs for k in r.jira_keys})
        devs = sorted({developer(r) for r in recs})
        rows.append(AreaRow(area=area, tickets=keys, developers=devs, commits=len(recs),
                            change_kinds=list(kinds), max_severity=sev, overlap=len(keys) > 1 or len(devs) > 1))
    rows.sort(key=lambda x: (-_rank(x.max_severity), -x.commits, x.area))

    cells: dict[tuple[str, str], tuple[int, Severity | None]] = {}
    for area, recs in by_area.items():
        for r in recs:
            for k in dict.fromkeys(r.jira_keys):
                n, sev = cells.get((area, k), (0, None))
                cells[(area, k)] = (n + 1, _max(sev, r.max_severity))
    matrix = [MatrixCell(area=a, ticket=k, commits=n, max_severity=s) for (a, k), (n, s) in sorted(cells.items())]

    all_keys = {t.key for t in tickets} | {k for r in records for k in r.jira_keys}
    severity_counts = {s.value: 0 for s in Severity}
    for r in records:
        if r.max_severity:
            severity_counts[r.max_severity.value] += 1
    return Aggregate(
        subject=subject, tickets=tickets, area_rows=rows, matrix=matrix,
        overlaps=[x.area for x in rows if x.overlap],
        counts={
            "tickets": len(all_keys), "developers": len({developer(r) for r in records}),
            "repos": len({r.repo for r in records}), "commits": len({(r.repo, r.commit) for r in records}),
        },
        severity_counts=severity_counts,
    )


def diff_reports(prev: ReportContent | None, new: ReportContent) -> ReportDiff:
    old = prev.aggregate if prev else None
    old_areas = {r.area for r in old.area_rows} if old else set()
    old_overlaps = set(old.overlaps) if old else set()
    old_items = {(i.type, i.what) for i in (prev.test_scope or [])} if prev else set()
    return ReportDiff(
        new_commits=new.aggregate.counts["commits"] - (old.counts["commits"] if old else 0),
        new_areas=[r.area for r in new.aggregate.area_rows if r.area not in old_areas],
        new_overlaps=[a for a in new.aggregate.overlaps if a not in old_overlaps],
        new_test_items=[i for i in (new.test_scope or []) if (i.type, i.what) not in old_items],
    )
