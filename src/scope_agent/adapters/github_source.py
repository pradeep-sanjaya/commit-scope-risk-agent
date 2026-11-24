"""GitHub (github.com or GitHub Enterprise Server) as the source-control host: push webhooks and the REST API.

Repos are "owner/name". All calls go to the REST API base (`https://api.github.com`, or
`https://<host>/api/v3` on Enterprise Server) with a token that can read the repositories.
"""

from datetime import datetime, timezone
from fnmatch import fnmatchcase
from urllib.parse import quote

import httpx

from scope_agent.adapters.source import decode, is_unusable
from scope_agent.domain.models import CommitData, FileChange, PushJob

PER_PAGE = 100
RAW = {"Accept": "application/vnd.github.raw+json"}
_BRANCH_PREFIX = "refs/heads/"


def parse_push(payload: dict, branch_globs: list[str]) -> list[PushJob]:
    """A `push` event is one ref. Tags, deleted branches and branches outside `branch_globs` yield nothing."""
    ref = payload["ref"]
    if not ref.startswith(_BRANCH_PREFIX) or payload.get("deleted"):
        return []
    branch = ref.removeprefix(_BRANCH_PREFIX)
    if not any(fnmatchcase(branch, g) for g in branch_globs):
        return []
    before = payload.get("before")
    pusher = payload.get("pusher") or {}
    return [PushJob(
        repo=payload["repository"]["full_name"],
        branch=branch,
        from_hash=None if payload.get("created") or not before or set(before) == {"0"} else before,
        to_hash=payload["after"],
        pushed_by=pusher.get("email") or pusher.get("name"),
    )]


def _status(file: dict) -> str:
    kind = file.get("status")
    if kind in ("added", "copied"):
        return "added"
    if kind == "removed":
        return "deleted"
    return "modified"  # modified, renamed, changed


class GitHubSourceClient:
    def __init__(self, http: httpx.Client):
        self._http = http

    @staticmethod
    def _base(repo: str) -> str:
        owner, name = repo.split("/", 1)
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        response = self._http.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response

    def _pages(self, url: str, params: dict | None = None):
        """Each page's JSON, following `Link: rel="next"` (whose URL already carries the query)."""
        response = self._get(url, {**(params or {}), "per_page": PER_PAGE})
        while True:
            yield response.json()
            nxt = response.links.get("next", {}).get("url")
            if not nxt:
                return
            response = self._get(nxt)

    def _compare(self, repo: str, base: str, head: str) -> list[dict]:
        """Commits in `head` but not `base`, oldest first."""
        url = f"{self._base(repo)}/compare/{quote(base, safe='')}...{quote(head, safe='')}"
        return [c for page in self._pages(url) for c in page.get("commits", [])]

    def _raw(self, repo: str, path: str, ref: str) -> bytes | None:
        response = self._http.get(f"{self._base(repo)}/contents/{quote(path, safe='/')}",
                                  params={"ref": ref}, headers=RAW)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def default_branch_head(self, repo: str) -> str:
        branch = self._get(self._base(repo)).json()["default_branch"]
        return self._get(f"{self._base(repo)}/commits/{quote(branch, safe='')}").json()["sha"]

    def commits_in_push(self, job: PushJob, limit: int) -> list[str]:
        since = job.from_hash or self.default_branch_head(job.repo)
        shas = [c["sha"] for c in self._compare(job.repo, since, job.to_hash)]
        return shas[-limit:] if limit > 0 else []

    def recent_commits(self, repo: str, branch: str, limit: int) -> list[str]:
        shas: list[str] = []
        for page in self._pages(f"{self._base(repo)}/commits", {"sha": branch}):
            for commit in page:
                shas.append(commit["sha"])
                if len(shas) >= limit:
                    return list(reversed(shas))
        return list(reversed(shas))

    def get_commit(self, repo: str, sha: str) -> CommitData:
        meta: dict = {}
        files: list[dict] = []
        for page in self._pages(f"{self._base(repo)}/commits/{quote(sha, safe='')}"):
            meta = meta or page
            files.extend(page.get("files", []))
        commit = meta["commit"]
        author = commit.get("author") or {}
        parents = [p["sha"] for p in meta.get("parents", [])]
        committed_at = datetime.fromisoformat((commit.get("committer") or author)["date"]).astimezone(timezone.utc)
        return CommitData(
            repo=repo,
            sha=sha,
            author_name=author.get("name", ""),
            author_email=author.get("email"),
            message=commit.get("message", ""),
            parents=parents,
            files=[self._file_change(repo, sha, parents, f) for f in files],
            committed_at=committed_at,
        )

    def _file_change(self, repo: str, sha: str, parents: list[str], file: dict) -> FileChange:
        path = file["filename"]
        status = _status(file)
        after = None if status == "deleted" else self._raw(repo, path, sha)
        before = None
        if status != "added" and parents:
            before = self._raw(repo, file.get("previous_filename") or path, parents[0])
        if is_unusable(before) or is_unusable(after):
            return FileChange(path=path, status=status, before=None, after=None)
        return FileChange(path=path, status=status, before=decode(before), after=decode(after))

    def get_file(self, repo: str, path: str, at: str) -> str | None:
        return decode(self._raw(repo, path, at))

    def list_files(self, repo: str, at: str, globs: list[str], limit: int = 50) -> list[str]:
        tree = self._get(f"{self._base(repo)}/git/trees/{quote(at, safe='')}", {"recursive": "1"}).json()
        matched: list[str] = []
        for entry in tree.get("tree", []):
            if entry.get("type") == "blob" and any(fnmatchcase(entry["path"], g) for g in globs):
                matched.append(entry["path"])
                if len(matched) >= limit:
                    break
        return matched

    def list_tags(self, repo: str) -> list[str]:
        return [t["name"] for page in self._pages(f"{self._base(repo)}/tags") for t in page]

    def commit_messages_between(self, repo: str, since_ref: str, until_ref: str, limit: int = 200) -> list[str]:
        commits = self._compare(repo, since_ref, until_ref)
        return [c["commit"]["message"] for c in reversed(commits)][:limit]
