"""Per-commit impact records: what a commit touched, for QA. Pure; no I/O."""

import re
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import BaseModel
from pydantic.dataclasses import dataclass

from scope_agent.domain.areas import AreaMap
from scope_agent.domain.file_selection import _matches
from scope_agent.domain.models import (
    CommitData, DependencyChange, FileChange, Finding, Severity, TicketContext,
)


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    change: Literal["added", "removed", "changed"]
    file: str


class ImpactRecord(BaseModel):
    repo: str
    commit: str
    author_name: str
    author_email: str | None
    committed_at: datetime
    jira_keys: list[str]
    epic_key: str | None
    subject: str
    areas: list[str]
    endpoints: list[Endpoint]
    migrations: list[str]
    config_files: list[str]
    dependency_changes: list[DependencyChange]
    finding_ids: list[str]
    max_severity: Severity | None
    test_note: list[str]


_JAVA_MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b(?:\s*\(([^)]*)\))?")
_CLASS_KEYWORD = re.compile(r"\bclass\b")
_PATH_ARG = re.compile(r'^\s*(?:(?:value|path)\s*=\s*)?(\{[^}]*\}|"[^"]*")')
_NAMED_PATH_ARG = re.compile(r'\b(?:value|path)\s*=\s*(\{[^}]*\}|"[^"]*")')
_STRING = re.compile(r'"([^"]*)"')
_REQUEST_METHOD = re.compile(r"RequestMethod\.(\w+)")
_NG_PATH = re.compile(r"""\bpath\s*:\s*['"]([^'"]*)['"]""")


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path
    if not path:
        return prefix
    return prefix.rstrip("/") + "/" + path.lstrip("/")


def _paths(args: str) -> list[str]:
    m = _PATH_ARG.match(args) or _NAMED_PATH_ARG.search(args)
    return _STRING.findall(m.group(1)) if m else [""]


def _spring(source: str | None) -> dict[tuple[str, str], str]:
    """(METHOD, full path) -> annotation text."""
    if not source:
        return {}
    class_at = _CLASS_KEYWORD.search(source)
    class_at = class_at.start() if class_at else len(source)
    prefix, found = "", {}
    prefix_seen = False
    for m in _JAVA_MAPPING.finditer(source):
        kind, args = m.group(1), m.group(2) or ""
        if m.start() < class_at:
            if kind == "Request" and not prefix_seen:
                prefix_seen = True
                prefix = _paths(args)[0]
            continue
        methods = (_REQUEST_METHOD.findall(args) or ["ANY"]) if kind == "Request" else [kind.upper()]
        for path in _paths(args):
            for method in methods:
                found[(method, _join(prefix, path))] = m.group(0)
    return found


def _angular(source: str | None) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for line in (source or "").splitlines():
        for m in _NG_PATH.finditer(line):
            found[("ROUTE", m.group(1))] = line.strip()
    return found


def _is_angular_routing(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return fnmatchcase(name, "*routing*.ts") or fnmatchcase(name, "*.routes.ts")


def endpoint_changes(fc: FileChange) -> list[Endpoint]:
    if fc.path.endswith(".java"):
        parse = _spring
    elif _is_angular_routing(fc.path):
        parse = _angular
    else:
        return []
    before, after = parse(fc.before), parse(fc.after)
    out = [
        Endpoint(m, p, "added" if (m, p) not in before else "changed", fc.path)
        for (m, p), text in after.items()
        if (m, p) not in before or before[(m, p)] != text
    ]
    out += [Endpoint(m, p, "removed", fc.path) for (m, p) in before if (m, p) not in after]
    return out


_MIGRATION = (
    re.compile(r"(^|.*/)db/migration/V[^/]*__[^/]*\.sql$"),
    re.compile(r"(^|.*/)db/changelog/"),
    re.compile(r"(^|.*/)liquibase/"),
)


def is_migration(path: str) -> bool:
    return any(p.match(path) for p in _MIGRATION)


def is_config(path: str, priority_globs: list[str]) -> bool:
    return _matches(path, priority_globs)


def _subject(repo: str, keys: list[str], epic_key: str | None) -> str:
    return epic_key or (keys[0] if keys else f"unassigned/{repo}")


def build_impact_record(
    commit: CommitData,
    keys: list[str],
    tickets: dict[str, TicketContext | None],
    dep_changes: list[DependencyChange],
    findings: list[Finding],
    test_note: list[str],
    area_map: AreaMap,
    priority_globs: list[str],
    committed_at: datetime,
) -> ImpactRecord:
    epic_key = next(
        (t.epic_key for k in keys if (t := tickets.get(k)) and t.epic_key), None,
    )
    paths = [f.path for f in commit.files]
    areas = list(dict.fromkeys(area_map.area_for(commit.repo, p) for p in paths))
    own = [f for f in findings if f.commit == commit.sha]
    return ImpactRecord(
        repo=commit.repo, commit=commit.sha, author_name=commit.author_name,
        author_email=commit.author_email, committed_at=committed_at, jira_keys=list(keys),
        epic_key=epic_key, subject=_subject(commit.repo, keys, epic_key), areas=areas,
        endpoints=[e for f in commit.files for e in endpoint_changes(f)],
        migrations=[p for p in paths if is_migration(p)],
        config_files=[p for p in paths if is_config(p, priority_globs)],
        dependency_changes=list(dep_changes), finding_ids=[f.id for f in own],
        max_severity=max((f.severity for f in own), key=lambda s: s.rank, default=None),
        test_note=list(test_note),
    )
