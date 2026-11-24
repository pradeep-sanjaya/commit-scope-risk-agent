from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.domain.file_selection import unified_diff
from scope_agent.domain.models import CommitData, DependencyChange, FileChange
from scope_agent.domain.swaps import any_import, changed_lines, import_prefixes

CONFIG_GLOBS = ["**/application*.y*ml", "**/application*.properties", "**/environment*.ts"]
MAX_CONFIG_FILES = 5


def _relevant(f: FileChange, change: DependencyChange) -> bool:
    """Import lines among the changed lines, or a changed line naming the most specific prefix."""
    removed, added = changed_lines(f.before, f.after)
    changed = removed + added
    if any_import(changed, change):
        return True
    specific = import_prefixes(change)[-1]
    return any(specific in line for line in changed)


class UsageContextBuilder:
    def __init__(self, bitbucket: BitbucketClient, max_chars: int):
        self._bitbucket = bitbucket
        self._max_chars = max_chars

    def build(self, commit: CommitData, change: DependencyChange) -> str:
        config = self._config(commit)
        diffs = [
            f"### diff {f.path}\n{unified_diff(f)}"
            for f in commit.files
            if f.before != f.after and _relevant(f, change)
        ]
        text = "\n\n".join(config)
        remaining = self._max_chars - len(text) - (2 if text and diffs else 0)
        if diffs and remaining > 0:
            text = "\n\n".join(p for p in (text, "\n\n".join(diffs)[:remaining]) if p)
        return text[: self._max_chars]

    def _config(self, commit: CommitData) -> list[str]:
        """Config gets a reserved half of the budget, split per file, so diffs cannot crowd it out."""
        paths = self._bitbucket.list_files(commit.repo, commit.sha, CONFIG_GLOBS, MAX_CONFIG_FILES)
        if not paths:
            return []
        per_file = max(self._max_chars // 2 // len(paths), 1)
        parts = []
        for path in paths:
            content = self._bitbucket.get_file(commit.repo, path, commit.sha)
            if content:
                parts.append(f"### config {path}\n{content}"[:per_file])
        return parts
