import httpx
import pytest
import respx

from scope_agent.adapters.nexus import NexusClient

BASE = "https://nexus.test"


@pytest.fixture
def client():
    return NexusClient(httpx.Client(base_url=BASE), "maven-all", "npm-all")


@respx.mock
def test_maven_pom_path_and_body(client):
    route = respx.get(
        f"{BASE}/repository/maven-all/dev/openfeature/contrib/flagd/0.8.1/flagd-0.8.1.pom"
    ).respond(200, text="<project/>")
    assert client.maven_pom("dev.openfeature.contrib", "flagd", "0.8.1") == "<project/>"
    assert route.called


@respx.mock
def test_maven_pom_404_is_none(client):
    respx.get(f"{BASE}/repository/maven-all/a/b/x/1/x-1.pom").respond(404)
    assert client.maven_pom("a.b", "x", "1") is None


@respx.mock
def test_maven_pom_server_error_raises(client):
    respx.get(f"{BASE}/repository/maven-all/a/x/1/x-1.pom").respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        client.maven_pom("a", "x", "1")


@respx.mock
def test_npm_metadata_scoped_name_encoded(client):
    route = respx.get(f"{BASE}/repository/npm-all/@scope%2Fpkg").respond(200, json={"name": "@scope/pkg"})
    assert client.npm_metadata("@scope/pkg") == {"name": "@scope/pkg"}
    assert route.called


@respx.mock
def test_npm_metadata_404_is_none(client):
    respx.get(f"{BASE}/repository/npm-all/left-pad").respond(404)
    assert client.npm_metadata("left-pad") is None
