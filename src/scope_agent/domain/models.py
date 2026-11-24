from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class Severity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"

    @property
    def rank(self) -> int:
        return {"high": 3, "medium": 2, "low": 1, "info": 0}[self.value]


class FindingType(str, Enum):
    dependency_behavior_change = "dependency_behavior_change"
    risky_change = "risky_change"
    out_of_scope = "out_of_scope"
    ticket_gap = "ticket_gap"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    repo: str  # "PROJ/slug"
    commit: str
    author_email: str | None
    jira_keys: list[str]
    type: FindingType
    category: str
    severity: Severity
    title: str
    explanation: str
    evidence: list[str]
    files: list[str]
    suggested_reviewers: list[str]
    created_at: datetime = Field(default_factory=_now)


class FileChange(BaseModel):
    path: str
    status: Literal["added", "modified", "deleted"]
    before: str | None
    after: str | None


class CommitData(BaseModel):
    repo: str
    sha: str
    author_name: str
    author_email: str | None
    message: str
    parents: list[str]
    files: list[FileChange]
    committed_at: datetime

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


class TicketContext(BaseModel):
    key: str
    summary: str
    description: str
    issue_type: str
    status: str
    acceptance_criteria: str | None
    components: list[str]
    labels: list[str]
    epic_key: str | None
    epic_summary: str | None
    epic_description: str | None
    linked_titles: list[str]


class DependencyChange(BaseModel):
    ecosystem: Literal["maven", "npm"]
    name: str
    old_version: str | None
    new_version: str | None
    kind: Literal["added", "removed", "upgraded", "downgraded", "parent", "bom", "transitive"]
    file: str


class LibrarySummary(BaseModel):
    library: str
    kind: Literal["upgrade", "new", "swap"]
    summary: str
    links: list[str]


class PushJob(BaseModel):
    repo: str
    branch: str
    from_hash: str | None
    to_hash: str
    pushed_by: str | None
