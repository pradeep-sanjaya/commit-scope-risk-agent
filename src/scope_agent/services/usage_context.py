import difflib

from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.domain.models import CommitData, DependencyChange, FileChange
from scope_agent.domain.swaps import import_prefixes

CONFIG_GLOBS = ["**/application*.y*ml", "**/application*.properties", "**/environment*.ts"]
MAX_CONFIG_FILES = 5


def _unified_diff(path: str, before: str | None, after: str | None) -> str:
    lines = difflib.unified_diff(
        (before or "").splitlines(), (after or "").splitlines(),
        fromfile=f"a/{path}", tofile=f"b/{path}", n=3, lineterm="",
    )
    return "\n".join(lines)


def _mentions(f: FileChange, prefixes: list[str]) -> bool:
    text = f"{f.before or ''}\n{f.after or ''}"
    return any(p in text for p in prefixes)


class UsageContextBuilder:
    def __init__(self, bitbucket: BitbucketClient, max_chars: int):
        self._bitbucket = bitbucket
        self._max_chars = max_chars

    def build(self, commit: CommitData, change: DependencyChange) -> str:
        prefixes = import_prefixes(change)
        parts = [
            f"### diff {f.path}\n{_unified_diff(f.path, f.before, f.after)}"
            for f in commit.files
            if _mentions(f, prefixes) and f.before != f.after
        ]
        for path in self._bitbucket.list_files(commit.repo, commit.sha, CONFIG_GLOBS, MAX_CONFIG_FILES):
            content = self._bitbucket.get_file(commit.repo, path, commit.sha)
            if content:
                parts.append(f"### config {path}\n{content}")
        return "\n\n".join(parts)[: self._max_chars]
