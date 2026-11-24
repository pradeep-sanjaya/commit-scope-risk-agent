"""What the agent needs from a source-control host (Bitbucket Data Center or GitHub), plus helpers shared by
the adapters. A repo is always "<owner-or-project>/<name>"."""

import hashlib
import hmac
from typing import Literal, Protocol

from scope_agent.domain.models import CommitData, PushJob

SourceProvider = Literal["bitbucket", "github"]

MAX_CONTENT_BYTES = 1024 * 1024
BINARY_SNIFF_BYTES = 8192


class SourceClient(Protocol):
    def default_branch_head(self, repo: str) -> str: ...

    def commits_in_push(self, job: PushJob, limit: int) -> list[str]:
        """Commits the push introduced (all of them for a new branch, relative to the default branch),
        newest `limit` only, oldest first."""
        ...

    def recent_commits(self, repo: str, branch: str, limit: int) -> list[str]:
        """Newest `limit` commits reachable from `branch`, oldest first."""
        ...

    def get_commit(self, repo: str, sha: str) -> CommitData: ...

    def get_file(self, repo: str, path: str, at: str) -> str | None: ...

    def list_files(self, repo: str, at: str, globs: list[str], limit: int = 50) -> list[str]: ...

    def list_tags(self, repo: str) -> list[str]: ...

    def commit_messages_between(self, repo: str, since_ref: str, until_ref: str, limit: int = 200) -> list[str]:
        """Messages of commits in `until_ref` but not in `since_ref`, newest first."""
        ...


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """`sha256=<hex HMAC-SHA256 of the body>`: Bitbucket's X-Hub-Signature and GitHub's X-Hub-Signature-256."""
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode("utf-8", "replace"))


def is_unusable(data: bytes | None) -> bool:
    """Binary (NUL in first 8 KB) or over 1 MB."""
    if data is None:
        return False
    return len(data) > MAX_CONTENT_BYTES or b"\x00" in data[:BINARY_SNIFF_BYTES]


def decode(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")
