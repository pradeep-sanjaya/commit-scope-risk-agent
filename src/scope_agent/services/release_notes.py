import re

from pydantic import BaseModel

from scope_agent.adapters.source import SourceClient
from scope_agent.adapters.github import GitHubClient
from scope_agent.domain.models import DependencyChange
from scope_agent.domain.versions import version_key, versions_between
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.services.source_resolver import SourceRepo, SourceResolver

NAMESPACE = "release_notes"
UNAVAILABLE_TTL_SECONDS = 6 * 3600
CHANGELOG_FILES = ("CHANGELOG.md", "CHANGES.md", "RELEASE_NOTES.md")

_TAG_VERSION = re.compile(r"(\d+(?:\.\d+)+(?:[-.][0-9A-Za-z.]+)?)$")
_HEADING_VERSION = re.compile(r"\d+(?:\.\d+)+(?:-[0-9A-Za-z.]+)?")


class ReleaseNotes(BaseModel):
    library: str
    entries: list[tuple[str, str]]
    links: list[str]
    available: bool


def _package_hint(change: DependencyChange) -> str:
    return change.name.split(":")[-1] if change.ecosystem == "maven" else change.name.split("/")[-1]


def split_changelog(text: str) -> list[tuple[str, str]]:
    """Sections keyed by the first version token of a markdown heading."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = _HEADING_VERSION.search(line) if line.startswith("#") else None
        if match:
            sections.append((match.group(0), []))
        elif sections:
            sections[-1][1].append(line)
    return [(version, "\n".join(body).strip()) for version, body in sections]


class ReleaseNotesService:
    def __init__(
        self,
        resolver: SourceResolver,
        github: GitHubClient,
        source: SourceClient,
        cache: KeyValueCache,
    ):
        self._resolver = resolver
        self._github = github
        self._source = source
        self._cache = cache

    def between(self, change: DependencyChange) -> ReleaseNotes:
        old, new = change.old_version, change.new_version
        if old is None or new is None:
            return self._unavailable(change)
        key = f"{change.name}@{old}..{new}"
        cached = self._cache.get(NAMESPACE, key)
        if cached is not None:
            return ReleaseNotes.model_validate_json(cached)
        notes = self._fetch(change, old, new)
        ttl = None if notes.available else UNAVAILABLE_TTL_SECONDS
        self._cache.put(NAMESPACE, key, notes.model_dump_json(), ttl)
        return notes

    @staticmethod
    def _unavailable(change: DependencyChange) -> ReleaseNotes:
        return ReleaseNotes(library=change.name, entries=[], links=[], available=False)

    def _fetch(self, change: DependencyChange, old: str, new: str) -> ReleaseNotes:
        source = self._resolver.resolve(change)
        if source.kind == "github":
            found = self._from_github(change, source, old, new)
        elif source.kind == "bitbucket":
            found = self._from_bitbucket(change, source, old, new)
        else:
            found = None
        if not found or not found[0]:
            return self._unavailable(change)
        entries, links = found
        return ReleaseNotes(library=change.name, entries=entries, links=links, available=True)

    def _from_github(self, change, source: SourceRepo, old, new) -> tuple[list, list] | None:
        releases = self._github.releases(source.ref)
        if releases is None:
            return None
        found = self._from_releases(change, releases, old, new)
        return found if found[0] else self._from_changelog(source, old, new)

    @staticmethod
    def _from_releases(change, releases: list[dict], old, new) -> tuple[list, list]:
        tagged = []
        for release in releases:
            if release.get("draft"):
                continue
            tag = release.get("tag_name") or ""
            match = _TAG_VERSION.search(tag)
            if match:
                tagged.append((tag, match.group(1), release))
        hint = _package_hint(change)
        if any(hint in tag for tag, _, _ in tagged):
            tagged = [t for t in tagged if hint in t[0]]
        wanted = set(versions_between([v for _, v, _ in tagged], old, new))
        chosen = sorted((t for t in tagged if t[1] in wanted), key=lambda t: version_key(t[1]))
        entries = [(v, r.get("body") or "") for _, v, r in chosen]
        links = [r["html_url"] for _, _, r in chosen if r.get("html_url")]
        return entries, links

    def _from_changelog(self, source: SourceRepo, old, new) -> tuple[list, list]:
        prefix = f"{source.subpath.strip('/')}/" if source.subpath else ""
        candidates = [f"{prefix}{n}" for n in CHANGELOG_FILES]
        if prefix:
            candidates += list(CHANGELOG_FILES)
        for path in candidates:
            text = self._github.file(source.ref, path)
            if text is None:
                continue
            sections = dict(split_changelog(text))
            wanted = versions_between(list(sections), old, new)
            if wanted:
                entries = [(v, sections[v]) for v in wanted]
                return entries, [f"https://github.com/{source.ref}/blob/HEAD/{path}"]
        return [], []

    def _from_bitbucket(self, change, source: SourceRepo, old, new) -> tuple[list, list] | None:
        tags = self._source.list_tags(source.ref)
        artifact = _package_hint(change)
        old_tag = self._tag_for(tags, old, artifact)
        new_tag = self._tag_for(tags, new, artifact)
        if old_tag is None or new_tag is None:
            return None
        messages = self._source.commit_messages_between(source.ref, old_tag, new_tag)
        if not messages:
            return None
        link = f"{source.ref} commits {old_tag}..{new_tag}"
        return [(f"{old}..{new}", "\n".join(messages))], [link]

    @staticmethod
    def _tag_for(tags: list[str], version: str, artifact: str) -> str | None:
        for candidate in (f"v{version}", version, f"{artifact}-{version}"):
            if candidate in tags:
                return candidate
        return None
