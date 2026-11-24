from pydantic import BaseModel

from scope_agent.adapters.bitbucket import BitbucketClient
from scope_agent.adapters.github import GitHubClient
from scope_agent.adapters.web_pages import WebPageFetcher
from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import DependencyChange
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.services.source_resolver import SourceResolver

NAMESPACE = "library_docs"


class LibraryDocs(BaseModel):
    library: str
    version: str
    text: str
    links: list[str]


class LibraryDocsService:
    def __init__(
        self,
        resolver: SourceResolver,
        github: GitHubClient,
        bitbucket: BitbucketClient,
        pages: WebPageFetcher,
        hints: DependencyHints,
        max_chars: int,
        cache: KeyValueCache,
    ):
        self._resolver = resolver
        self._github = github
        self._bitbucket = bitbucket
        self._pages = pages
        self._hints = hints
        self._max_chars = max_chars
        self._cache = cache

    def docs_for(self, change: DependencyChange) -> LibraryDocs:
        version = change.new_version or change.old_version or ""
        key = f"{change.name}@{version}"
        cached = self._cache.get(NAMESPACE, key)
        if cached is not None:
            return LibraryDocs.model_validate_json(cached)
        parts: list[str] = []
        links: list[str] = []
        readme, readme_link = self._readme(change, version)
        if readme:
            parts.append(readme)
            if readme_link:
                links.append(readme_link)
        for url in self._hints.library_docs.get(change.name, []):
            page = self._pages.text(url)
            if page:
                parts.append(page)
                links.append(url)
        docs = LibraryDocs(
            library=change.name, version=version, text="\n\n".join(parts)[: self._max_chars], links=links
        )
        if docs.text:  # don't pin a transient miss forever
            self._cache.put(NAMESPACE, key, docs.model_dump_json(), None)
        return docs

    def _readme(self, change: DependencyChange, version: str) -> tuple[str | None, str | None]:
        source = self._resolver.resolve(change)
        if source.kind == "github":
            return self._github.readme(source.ref, source.subpath), f"https://github.com/{source.ref}"
        if source.kind == "bitbucket":
            artifact = change.name.split(":")[-1]
            tags = self._bitbucket.list_tags(source.ref)
            for tag in (f"v{version}", version, f"{artifact}-{version}"):
                if tag in tags:
                    return self._bitbucket.get_file(source.ref, "README.md", tag), None
        return None, None
