import httpx
import pytest
import respx

from scope_agent.adapters.nexus import NexusClient
from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import DependencyChange
from scope_agent.services.source_resolver import SourceRepo, SourceResolver

BASE = "https://nexus.test"


def _change(name="dev.openfeature.contrib:flagd", eco="maven", old="0.7.0", new="0.8.1"):
    return DependencyChange(ecosystem=eco, name=name, old_version=old, new_version=new, kind="upgraded", file="pom.xml")


def _resolver(hints=None):
    nexus = NexusClient(httpx.Client(base_url=BASE), "m", "n")
    return SourceResolver(nexus, hints or DependencyHints(), "bitbucket.corp")


def _pom(scm_url=None, url=None, ns=True):
    xmlns = ' xmlns="http://maven.apache.org/POM/4.0.0"' if ns else ""
    scm = f"<scm><url>{scm_url}</url></scm>" if scm_url else ""
    u = f"<url>{url}</url>" if url else ""
    return f"<project{xmlns}><artifactId>flagd</artifactId>{u}{scm}</project>"


def _mock_pom(body, version="0.8.1"):
    respx.get(f"{BASE}/repository/m/dev/openfeature/contrib/flagd/{version}/flagd-{version}.pom").respond(200, text=body)


@respx.mock
def test_resolve_maven_scm_github():
    _mock_pom(_pom(scm_url="https://github.com/open-feature/java-sdk-contrib/tree/main/providers/flagd"))
    assert _resolver().resolve(_change()) == SourceRepo("github", "open-feature/java-sdk-contrib", None)


@pytest.mark.parametrize("url", [
    "https://github.com/o/r",
    "git+https://github.com/o/r.git",
    "git@github.com:o/r.git",
    "scm:git:https://github.com/o/r.git",
    "scm:git:git@github.com:o/r.git",
])
@respx.mock
def test_github_url_forms(url):
    _mock_pom(_pom(scm_url=url, ns=False))
    assert _resolver().resolve(_change()) == SourceRepo("github", "o/r", None)


@respx.mock
def test_resolve_falls_back_to_project_url():
    _mock_pom(_pom(url="https://github.com/o/r"))
    assert _resolver().resolve(_change()).ref == "o/r"


def test_resolve_internal_hint_wins():
    hints = DependencyHints(internal_repos={"com.corp:flag-util": "PLAT/flag-util"})
    # no nexus mocking: any HTTP call would fail the test
    assert _resolver(hints).resolve(_change(name="com.corp:flag-util")) == SourceRepo("bitbucket", "PLAT/flag-util", None)


@respx.mock
def test_resolve_bitbucket_scm_url():
    _mock_pom(_pom(scm_url="https://bitbucket.corp/scm/plat/flag-util.git"))
    assert _resolver().resolve(_change()) == SourceRepo("bitbucket", "PLAT/flag-util", None)


@respx.mock
def test_resolve_bitbucket_projects_url():
    _mock_pom(_pom(scm_url="https://bitbucket.corp/projects/PLAT/repos/flag-util/browse"))
    assert _resolver().resolve(_change()) == SourceRepo("bitbucket", "PLAT/flag-util", None)


@respx.mock
def test_unknown_when_pom_missing_or_host_foreign():
    respx.get(f"{BASE}/repository/m/dev/openfeature/contrib/flagd/0.8.1/flagd-0.8.1.pom").respond(404)
    assert _resolver().resolve(_change()) == SourceRepo("unknown", "", None)
    respx.get(f"{BASE}/repository/m/dev/openfeature/contrib/flagd/0.9.0/flagd-0.9.0.pom").respond(
        200, text=_pom(scm_url="https://gitlab.com/o/r"))
    assert _resolver().resolve(_change(new="0.9.0")).kind == "unknown"


@respx.mock
def test_uses_old_version_when_new_is_none():
    _mock_pom(_pom(scm_url="https://github.com/o/r"), version="0.7.0")
    assert _resolver().resolve(_change(new=None)).ref == "o/r"


@respx.mock
def test_resolve_npm_repository_with_directory():
    respx.get(f"{BASE}/repository/n/@scope%2Fflagd").respond(200, json={
        "repository": {"type": "git", "url": "git+https://github.com/o/js-sdk.git", "directory": "packages/flagd"},
    })
    got = _resolver().resolve(_change(name="@scope/flagd", eco="npm"))
    assert got == SourceRepo("github", "o/js-sdk", "packages/flagd")


@respx.mock
def test_resolve_npm_string_repository_and_missing():
    respx.get(f"{BASE}/repository/n/a").respond(200, json={"repository": "github:o/a"})
    respx.get(f"{BASE}/repository/n/b").respond(404)
    assert _resolver().resolve(_change(name="a", eco="npm")) == SourceRepo("github", "o/a", None)
    assert _resolver().resolve(_change(name="b", eco="npm")).kind == "unknown"
