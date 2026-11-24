import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from scope_agent.adapters.nexus import NexusClient
from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import DependencyChange


@dataclass(frozen=True)
class SourceRepo:
    kind: Literal["github", "bitbucket", "unknown"]
    ref: str
    subpath: str | None


_UNKNOWN = SourceRepo("unknown", "", None)
_GITHUB_HOSTS = {"github.com", "www.github.com"}


def _strip_noise(url: str) -> str:
    url = url.strip()
    if url.startswith("scm:"):
        url = url.split(":", 2)[2] if url.count(":") >= 2 else url
    if url.startswith("git+"):
        url = url[4:]
    return url


def _from_scp(url: str) -> tuple[str, str] | None:
    """`git@host:path` -> (host, path)."""
    if "://" in url or "@" not in url or ":" not in url:
        return None
    host_part, path = url.split(":", 1)
    return host_part.split("@", 1)[1].lower(), path


def _parse_repo_url(url: str, bitbucket_host: str) -> SourceRepo | None:
    url = _strip_noise(url)
    scp = _from_scp(url)
    if scp:
        host, path = scp
    else:
        parsed = urlparse(url)
        host, path = (parsed.hostname or "").lower(), parsed.path
    parts = [p for p in path.split("/") if p]
    if host in _GITHUB_HOSTS and len(parts) >= 2:
        return SourceRepo("github", f"{parts[0]}/{parts[1].removesuffix('.git')}", None)
    if host and host == bitbucket_host.lower():
        if len(parts) >= 4 and parts[0] == "projects" and parts[2] == "repos":
            return SourceRepo("bitbucket", f"{parts[1].upper()}/{parts[3].removesuffix('.git')}", None)
        if len(parts) >= 3 and parts[0] == "scm":
            return SourceRepo("bitbucket", f"{parts[1].upper()}/{parts[2].removesuffix('.git')}", None)
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _pom_urls(pom: str) -> list[str]:
    """scm/url, scm/connection, then project url (namespace-agnostic)."""
    root = ET.fromstring(pom)
    direct = {_local(c.tag): c for c in root}
    urls: list[str] = []
    scm = direct.get("scm")
    if scm is not None:
        for child in scm:
            if _local(child.tag) in ("url", "connection") and child.text:
                urls.append(child.text)
    if "url" in direct and direct["url"].text:
        urls.append(direct["url"].text)
    return urls


class SourceResolver:
    def __init__(self, nexus: NexusClient, hints: DependencyHints, bitbucket_host: str):
        self._nexus = nexus
        self._hints = hints
        self._host = bitbucket_host

    def resolve(self, change: DependencyChange) -> SourceRepo:
        internal = self._hints.internal_repos.get(change.name)
        if internal:
            return SourceRepo("bitbucket", internal, None)
        version = change.new_version or change.old_version
        if version is None:
            return _UNKNOWN
        if change.ecosystem == "maven":
            return self._from_maven(change.name, version)
        return self._from_npm(change.name, version)

    def _from_maven(self, name: str, version: str) -> SourceRepo:
        group, _, artifact = name.partition(":")
        pom = self._nexus.maven_pom(group, artifact, version)
        if pom is None:
            return _UNKNOWN
        try:
            urls = _pom_urls(pom)
        except ET.ParseError:
            return _UNKNOWN
        for url in urls:
            found = _parse_repo_url(url, self._host)
            if found:
                return found
        return _UNKNOWN

    def _from_npm(self, name: str, version: str) -> SourceRepo:
        meta = self._nexus.npm_metadata(name)
        if meta is None:
            return _UNKNOWN
        repository = (meta.get("versions", {}).get(version) or {}).get("repository") or meta.get("repository")
        if not repository:
            return _UNKNOWN
        if isinstance(repository, str):
            url, directory = repository, None
        else:
            url, directory = repository.get("url"), repository.get("directory")
        if not url:
            return _UNKNOWN
        if url.startswith("github:"):
            url = "https://github.com/" + url[len("github:"):]
        elif "/" in url and ":" not in url and url.count("/") == 1:  # bare owner/repo shorthand
            url = "https://github.com/" + url
        found = _parse_repo_url(url, self._host)
        if not found:
            return _UNKNOWN
        return SourceRepo(found.kind, found.ref, directory or None)
