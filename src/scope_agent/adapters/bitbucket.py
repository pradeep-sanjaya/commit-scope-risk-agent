import hashlib
import hmac
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from urllib.parse import quote

import httpx

from scope_agent.domain.models import CommitData, FileChange, PushJob

MAX_CONTENT_BYTES = 1024 * 1024
BINARY_SNIFF_BYTES = 8192
PAGE_SIZE = 100
_ZERO_HASH = "0" * 40


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.encode("utf-8", "replace"))


def parse_refs_changed(payload: dict, branch_globs: list[str]) -> list[PushJob]:
    repository = payload["repository"]
    repo = f"{repository['project']['key']}/{repository['slug']}"
    actor = payload.get("actor") or {}
    pushed_by = actor.get("emailAddress") or actor.get("name")
    jobs: list[PushJob] = []
    for change in payload.get("changes", []):
        ref = change["ref"]
        if ref.get("type") != "BRANCH" or change.get("type") == "DELETE":
            continue
        if not any(fnmatchcase(ref["displayId"], g) for g in branch_globs):
            continue
        from_hash = change.get("fromHash")
        if not from_hash or set(from_hash) == {"0"}:
            from_hash = None
        jobs.append(
            PushJob(
                repo=repo,
                branch=ref["displayId"],
                from_hash=from_hash,
                to_hash=change["toHash"],
                pushed_by=pushed_by,
            )
        )
    return jobs


def _is_unusable(data: bytes | None) -> bool:
    """Binary (NUL in first 8 KB) or over 1 MB."""
    if data is None:
        return False
    return len(data) > MAX_CONTENT_BYTES or b"\x00" in data[:BINARY_SNIFF_BYTES]


def _decode(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")


class BitbucketClient:
    def __init__(self, http: httpx.Client):
        self._http = http

    @staticmethod
    def _base(repo: str) -> str:
        project, slug = repo.split("/", 1)
        return f"/rest/api/1.0/projects/{quote(project, safe='')}/repos/{quote(slug, safe='')}"

    def _get_json(self, url: str, params: dict | None = None) -> dict:
        response = self._http.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _paged(self, url: str, params: dict | None = None):
        start = 0
        while True:
            page = self._get_json(url, {**(params or {}), "start": start, "limit": PAGE_SIZE})
            yield from page.get("values", [])
            if page.get("isLastPage", True):
                return
            start = page["nextPageStart"]

    def _raw(self, repo: str, path: str, at: str) -> bytes | None:
        response = self._http.get(f"{self._base(repo)}/raw/{quote(path, safe='/')}", params={"at": at})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def default_branch_head(self, repo: str) -> str:
        return self._get_json(f"{self._base(repo)}/branches/default")["latestCommit"]

    def commits_in_push(self, job: PushJob, limit: int) -> list[str]:
        since = job.from_hash or self.default_branch_head(job.repo)
        shas: list[str] = []
        for commit in self._paged(
            f"{self._base(job.repo)}/commits", {"until": job.to_hash, "since": since}
        ):
            shas.append(commit["id"])
            if len(shas) >= limit:
                break
        return list(reversed(shas))  # API is newest-first; keep newest `limit`, return oldest-first

    def recent_commits(self, repo: str, branch: str, limit: int) -> list[str]:
        """Newest `limit` commit ids reachable from `branch`, oldest first."""
        shas: list[str] = []
        for commit in self._paged(f"{self._base(repo)}/commits", {"until": branch}):
            shas.append(commit["id"])
            if len(shas) >= limit:
                break
        return list(reversed(shas))

    def get_commit(self, repo: str, sha: str) -> CommitData:
        base = self._base(repo)
        meta = self._get_json(f"{base}/commits/{quote(sha, safe='')}")
        parents = [p["id"] for p in meta.get("parents", [])]
        author = meta.get("author") or {}
        committed_at = datetime.fromtimestamp(meta["committerTimestamp"] / 1000, tz=timezone.utc)
        files = [
            self._file_change(repo, sha, parents, change)
            for change in self._paged(f"{base}/commits/{quote(sha, safe='')}/changes")
        ]
        return CommitData(
            repo=repo,
            sha=sha,
            author_name=author.get("name", ""),
            author_email=author.get("emailAddress"),
            message=meta.get("message", ""),
            parents=parents,
            files=files,
            committed_at=committed_at,
        )

    def _file_change(self, repo: str, sha: str, parents: list[str], change: dict) -> FileChange:
        path = change["path"]["toString"]
        kind = change.get("type")
        status = "added" if kind == "ADD" else "deleted" if kind == "DELETE" else "modified"
        after = None if status == "deleted" else self._raw(repo, path, sha)
        before = None
        if status != "added" and parents:
            before_path = (change.get("srcPath") or change["path"])["toString"]
            before = self._raw(repo, before_path, parents[0])
        if _is_unusable(before) or _is_unusable(after):
            return FileChange(path=path, status=status, before=None, after=None)
        return FileChange(path=path, status=status, before=_decode(before), after=_decode(after))

    def get_file(self, repo: str, path: str, at: str) -> str | None:
        return _decode(self._raw(repo, path, at))

    def list_files(self, repo: str, at: str, globs: list[str], limit: int = 50) -> list[str]:
        matched: list[str] = []
        for path in self._paged(f"{self._base(repo)}/files", {"at": at}):
            if any(fnmatchcase(path, g) for g in globs):
                matched.append(path)
                if len(matched) >= limit:
                    break
        return matched

    def list_tags(self, repo: str) -> list[str]:
        return [t["displayId"] for t in self._paged(f"{self._base(repo)}/tags")]

    def commit_messages_between(
        self, repo: str, since_ref: str, until_ref: str, limit: int = 200
    ) -> list[str]:
        messages: list[str] = []
        for commit in self._paged(
            f"{self._base(repo)}/commits", {"since": since_ref, "until": until_ref}
        ):
            messages.append(commit["message"])
            if len(messages) >= limit:
                break
        return messages
