from dataclasses import dataclass, field
from typing import Literal

from scope_agent.domain.chunking import chunk, was_truncated
from scope_agent.domain.file_selection import select_files, unified_diff
from scope_agent.domain.models import CommitData, Finding, FindingType, Severity, TicketContext
from scope_agent.llm.base import LLMClient, LLMOutputError
from scope_agent.llm.prompts import render_prompt
from scope_agent.llm.schemas import CodeVerdict, LLMFinding

SYSTEM = "You are a careful code reviewer. Output only through the report tool."
_AC_RANK = {"ok": 0, "vague": 1, "missing": 2}


@dataclass
class CodeAnalysis:
    findings: list[Finding] = field(default_factory=list)
    ac_quality: Literal["ok", "vague", "missing"] | None = None
    test_note: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class CodeAnalyzer:
    def __init__(self, llm: LLMClient, exclude: list[str], priority: list[str],
                 max_input_tokens: int, max_chunks: int):
        self._llm = llm
        self._exclude = exclude
        self._priority = priority
        self._max_tokens = max_input_tokens
        self._max_chunks = max_chunks

    def analyze(self, commit: CommitData, tickets: list[TicketContext]) -> CodeAnalysis:
        files = select_files(commit.files, self._exclude, self._priority)
        result = CodeAnalysis()
        if not files:
            return result
        diffs = [(f.path, unified_diff(f)) for f in files]
        chunks, skipped = chunk(diffs, self._max_tokens, self._max_chunks)
        partial = bool(skipped) or any(was_truncated(t) for c in chunks for _, t in c)
        keys = [t.key for t in tickets]
        qualities: list[str] = []
        for part in chunks:
            prompt = render_prompt("code_review", commit_message=commit.message, tickets=tickets,
                                   diffs=part, partial=partial)
            try:
                verdict = self._llm.analyze(SYSTEM, prompt, CodeVerdict).data
            except LLMOutputError as e:
                result.errors.append(f"code analysis chunk failed: {e}")
                continue
            for lf in verdict.findings:
                if lf.type == FindingType.out_of_scope and not tickets:
                    continue
                result.findings.append(self._finding(commit, keys, lf))
            qualities.append(verdict.ac_quality)
            result.test_note.extend(n for n in verdict.test_note if n not in result.test_note)
        if tickets and qualities:
            result.ac_quality = max(qualities, key=_AC_RANK.__getitem__)
        if skipped:
            result.findings.append(self._partial(commit, keys, skipped))
        return result

    @staticmethod
    def _finding(commit: CommitData, keys: list[str], lf: LLMFinding) -> Finding:
        return Finding(
            repo=commit.repo, commit=commit.sha, author_email=commit.author_email, jira_keys=keys,
            type=lf.type, category=lf.category, severity=lf.severity, title=lf.title,
            explanation=lf.explanation, evidence=lf.evidence, files=lf.files, suggested_reviewers=[],
        )

    @staticmethod
    def _partial(commit: CommitData, keys: list[str], skipped: list[str]) -> Finding:
        return Finding(
            repo=commit.repo, commit=commit.sha, author_email=commit.author_email, jira_keys=keys,
            type=FindingType.risky_change, category="partial_analysis", severity=Severity.info,
            title=f"Partial analysis: {len(skipped)} file(s) not reviewed (size budget)",
            explanation=("The remaining files exceeded the per-commit review budget and were not "
                         "analyzed, so they should be reviewed manually."),
            evidence=list(skipped), files=list(skipped), suggested_reviewers=[],
        )
