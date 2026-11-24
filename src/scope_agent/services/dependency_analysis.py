from dataclasses import dataclass, field

import structlog

from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import (
    CommitData, DependencyChange, Finding, FindingType, LibrarySummary, Severity,
)
from scope_agent.domain.swaps import Swap, detect_swaps
from scope_agent.domain.versions import is_major_bump
from scope_agent.llm.base import BudgetExceeded, LLMClient, LLMOutputError
from scope_agent.llm.prompts import render_prompt
from scope_agent.llm.schemas import DependencyVerdict
from scope_agent.services.library_docs import LibraryDocsService
from scope_agent.services.library_profile import LibraryProfileBuilder
from scope_agent.services.release_notes import ReleaseNotes, ReleaseNotesService
from scope_agent.services.usage_context import UsageContextBuilder

log = structlog.get_logger()

SYSTEM = (
    "You are a careful reviewer of dependency changes. Your output is reviewed by a person. "
    "Report only through the provided tool."
)
NO_DOCS = "(documentation unavailable)"
NO_USAGE = "(no usage context found)"
NO_PROFILE = "(no profile of the removed library available)"


@dataclass
class DependencyAnalysis:
    findings: list[Finding] = field(default_factory=list)
    summaries: dict[str, list[LibrarySummary]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _arrow(c: DependencyChange) -> str:
    return f"{c.old_version}→{c.new_version}"


class DependencyAnalyzer:
    def __init__(
        self,
        llm: LLMClient,
        notes: ReleaseNotesService,
        docs: LibraryDocsService,
        usage: UsageContextBuilder,
        profiles: LibraryProfileBuilder,
        hints: DependencyHints,
    ):
        self._llm = llm
        self._notes = notes
        self._docs = docs
        self._usage = usage
        self._profiles = profiles
        self._hints = hints

    def analyze(
        self, commits: list[CommitData], changes_by_commit: dict[str, list[DependencyChange]]
    ) -> DependencyAnalysis:
        result = DependencyAnalysis()
        by_sha = {c.sha: c for c in commits}
        all_changes = [c for sha in changes_by_commit for c in changes_by_commit[sha]]
        swaps = detect_swaps(all_changes, commits, self._hints.known_swaps)
        swap_by_added = {id(s.added): s for s in swaps}
        swapped = {id(s.added) for s in swaps} | {id(s.removed) for s in swaps}
        removed_commit = {id(c): by_sha[sha] for sha, cs in changes_by_commit.items() if sha in by_sha for c in cs}
        for sha, changes in changes_by_commit.items():
            commit = by_sha.get(sha)
            if commit is None:
                continue
            for change in changes:
                swap = swap_by_added.get(id(change))
                if swap is not None:
                    self._swap(result, commit, swap, removed_commit.get(id(swap.removed), commit))
                elif id(change) in swapped:
                    continue
                else:
                    self._change(result, commit, change)
        return result

    def _change(self, result: DependencyAnalysis, commit: CommitData, change: DependencyChange) -> None:
        if change.kind in ("upgraded", "downgraded"):
            self._upgrade(result, commit, change)
        elif change.kind in ("parent", "bom"):
            result.findings.append(self._rule_finding(
                commit, change, "platform_upgrade", Severity.medium,
                f"Platform upgrade {change.name} {_arrow(change)}: many managed versions changed",
                "A parent or BOM version changed, which can move many managed dependency versions at once.",
            ))
            self._upgrade(result, commit, change)
        elif change.kind == "added":
            self._added(result, commit, change)
        elif change.kind == "transitive":
            result.findings.append(self._rule_finding(
                commit, change, "transitive_major", Severity.low,
                f"Transitive major upgrade {change.name} {_arrow(change)}",
                "A transitive dependency changed version; its behavior was not analyzed.",
            ))

    def _upgrade(self, result: DependencyAnalysis, commit: CommitData, change: DependencyChange) -> None:
        notes = self._fetch_notes(change)
        if not notes.available or not notes.entries:
            major = bool(change.old_version and change.new_version
                         and is_major_bump(change.old_version, change.new_version))
            result.findings.append(self._rule_finding(
                commit, change, "behavior_unknown", Severity.medium if major else Severity.low,
                f"Behavior changes unknown for {change.name} {_arrow(change)}: release notes unavailable",
                "No release notes could be found for this version range, so behavior changes cannot be ruled out.",
            ))
            return
        prompt = render_prompt(
            "dependency_upgrade", library=change.name, old_version=change.old_version,
            new_version=change.new_version, notes=notes.entries, usage=self._usage_for(commit, change),
        )
        self._run(result, commit, change, prompt, "upgrade", change.name, notes.links)

    def _added(self, result: DependencyAnalysis, commit: CommitData, change: DependencyChange) -> None:
        text, links = self._fetch_docs(change)
        prompt = render_prompt(
            "dependency_new", library=change.name, version=change.new_version, docs=text,
            usage=self._usage_for(commit, change),
        )
        self._run(result, commit, change, prompt, "new", change.name, links)

    def _swap(self, result: DependencyAnalysis, commit: CommitData, swap: Swap, removing: CommitData) -> None:
        removed, added = swap.removed, swap.added
        try:
            old_profile = self._profiles.profile(removed)
        except Exception as exc:
            log.warning("library_profile_failed", library=removed.name, error=str(exc))
            old_profile = None
        if old_profile is None:
            old_profile = self._usage_for(removing, removed)
        text, links = self._fetch_docs(added)
        prompt = render_prompt(
            "dependency_swap", removed=removed.name, removed_version=removed.old_version,
            added=added.name, added_version=added.new_version,
            old_profile=old_profile or NO_PROFILE, new_docs=text,
        )
        self._run(result, commit, added, prompt, "swap", f"{removed.name} → {added.name}", links)

    def _run(self, result, commit, change, prompt, kind, library, links) -> None:
        try:
            verdict = self._llm.analyze(SYSTEM, prompt, DependencyVerdict).data
        except BudgetExceeded:
            raise
        except LLMOutputError as exc:
            result.errors.append(f"{library}: {exc}")
            return
        assert isinstance(verdict, DependencyVerdict)
        for lf in verdict.findings:
            files = [change.file] + [f for f in lf.files if f != change.file]
            result.findings.append(self._finding(
                commit, FindingType.dependency_behavior_change, lf.category, lf.severity, lf.title,
                lf.explanation, lf.evidence + links, files,
            ))
        result.summaries.setdefault(commit.sha, []).append(
            LibrarySummary(library=library, kind=kind, summary=verdict.reviewer_summary, links=links)
        )

    def _fetch_notes(self, change: DependencyChange) -> ReleaseNotes:
        try:
            return self._notes.between(change)
        except Exception as exc:
            log.warning("release_notes_failed", library=change.name, error=str(exc))
            return ReleaseNotes(library=change.name, entries=[], links=[], available=False)

    def _fetch_docs(self, change: DependencyChange) -> tuple[str, list[str]]:
        try:
            docs = self._docs.docs_for(change)
        except Exception as exc:
            log.warning("library_docs_failed", library=change.name, error=str(exc))
            return NO_DOCS, []
        return docs.text or NO_DOCS, docs.links

    def _usage_for(self, commit: CommitData, change: DependencyChange) -> str:
        try:
            return self._usage.build(commit, change) or NO_USAGE
        except Exception as exc:
            log.warning("usage_context_failed", library=change.name, error=str(exc))
            return NO_USAGE

    def _rule_finding(self, commit, change, category, severity, title, explanation) -> Finding:
        evidence = [f"{change.name} {change.old_version} → {change.new_version}", change.file]
        return self._finding(commit, FindingType.dependency_behavior_change, category, severity,
                             title, explanation, evidence, [change.file])

    @staticmethod
    def _finding(commit, ftype, category, severity, title, explanation, evidence, files) -> Finding:
        return Finding(
            repo=commit.repo, commit=commit.sha, author_email=commit.author_email, jira_keys=[],
            type=ftype, category=category, severity=severity, title=title, explanation=explanation,
            evidence=evidence, files=files, suggested_reviewers=[],
        )
