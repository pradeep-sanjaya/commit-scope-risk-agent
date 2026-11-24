import re

from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.domain.models import DependencyChange
from scope_agent.services.source_resolver import SourceResolver

SOURCE_GLOBS = ["*.java", "*.kt", "*.scala", "*.ts", "*.js", "*.py", "*.go", "*.properties", "*.yml", "*.yaml"]
MAX_FILES = 8
LIST_LIMIT = 200
_KEYWORDS = re.compile(r"poll|schedul|interval|stream|grpc|websocket|http|connect|client", re.IGNORECASE)


class LibraryProfileBuilder:
    def __init__(self, resolver: SourceResolver, bitbucket: BitbucketClient, max_chars: int):
        self._resolver = resolver
        self._bitbucket = bitbucket
        self._max_chars = max_chars

    def profile(self, change: DependencyChange) -> str | None:
        source = self._resolver.resolve(change)
        if source.kind != "bitbucket" or not change.old_version:
            return None
        tag = self._tag(source.ref, change)
        if tag is None:
            return None
        parts: list[str] = []
        readme = self._bitbucket.get_file(source.ref, "README.md", tag)
        if readme:
            parts.append(f"### README.md\n{readme}")
        matched = 0
        for path in self._bitbucket.list_files(source.ref, tag, SOURCE_GLOBS, LIST_LIMIT):
            if matched >= MAX_FILES:
                break
            content = self._bitbucket.get_file(source.ref, path, tag)
            if content and (_KEYWORDS.search(path) or _KEYWORDS.search(content)):
                parts.append(f"### {path}\n{content}")
                matched += 1
        return "\n\n".join(parts)[: self._max_chars] if parts else None

    def _tag(self, repo: str, change: DependencyChange) -> str | None:
        tags = self._bitbucket.list_tags(repo)
        artifact = change.name.split(":")[-1]
        for candidate in (f"v{change.old_version}", change.old_version, f"{artifact}-{change.old_version}"):
            if candidate in tags:
                return candidate
        return None
