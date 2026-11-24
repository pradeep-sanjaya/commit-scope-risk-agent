import httpx
import pytest
import respx

from scope_agent.adapters.github import GitHubClient

BASE = "https://api.github.test"


@pytest.fixture
def client():
    return GitHubClient(httpx.Client(base_url=BASE))


@respx.mock
@pytest.mark.parametrize("status", [403, 404, 429])
def test_releases_soft_failures_are_none(client, status):
    respx.get(f"{BASE}/repos/o/r/releases").respond(status)
    assert client.releases("o/r") is None


@respx.mock
def test_releases_server_error_raises(client):
    respx.get(f"{BASE}/repos/o/r/releases").respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        client.releases("o/r")


@respx.mock
def test_releases_paginates_until_short_page_and_respects_max_pages(client):
    full = [{"tag_name": f"v{i}"} for i in range(100)]
    route = respx.get(f"{BASE}/repos/o/r/releases").mock(
        side_effect=[httpx.Response(200, json=full), httpx.Response(200, json=[{"tag_name": "last"}])]
    )
    assert len(client.releases("o/r")) == 101
    assert route.call_count == 2
    route2 = respx.get(f"{BASE}/repos/o/q/releases").mock(return_value=httpx.Response(200, json=full))
    assert len(client.releases("o/q", max_pages=2)) == 200
    assert route2.call_count == 2


@respx.mock
def test_file_raw_and_encoded_path(client):
    route = respx.get(f"{BASE}/repos/o/r/contents/sub%20dir/CHANGELOG.md").respond(200, text="# Changes")
    assert client.file("o/r", "sub dir/CHANGELOG.md") == "# Changes"
    assert "raw" in route.calls[0].request.headers["accept"]


@respx.mock
def test_file_404_none(client):
    respx.get(f"{BASE}/repos/o/r/contents/X.md").respond(404)
    assert client.file("o/r", "X.md") is None


@respx.mock
def test_readme_root_and_subpath(client):
    respx.get(f"{BASE}/repos/o/r/readme").respond(200, text="root")
    respx.get(f"{BASE}/repos/o/r/readme/pkgs/a").respond(200, text="sub")
    assert client.readme("o/r", None) == "root"
    assert client.readme("o/r", "pkgs/a") == "sub"
