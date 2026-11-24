from datetime import datetime, timezone

import httpx
import pytest
import respx

from scope_agent.adapters.github_source import GitHubSourceClient, parse_push
from scope_agent.domain.models import PushJob

API = "https://api.github.test"
REPO = f"{API}/repos/acme/payments"
ZERO = "0" * 40


@pytest.fixture
def client():
    return GitHubSourceClient(httpx.Client(base_url=API))


def push(ref="refs/heads/main", before="aaa111", after="bbb222", **extra):
    return {"ref": ref, "before": before, "after": after, "created": before == ZERO, "deleted": after == ZERO,
            "repository": {"full_name": "acme/payments"},
            "pusher": {"name": "jdoe", "email": "jdoe@example.com"}, **extra}


def test_parse_push_branch_filter_tags_deletes_and_new_branches():
    globs = ["main", "release/*"]
    [job] = parse_push(push(), globs)
    assert job == PushJob(repo="acme/payments", branch="main", from_hash="aaa111", to_hash="bbb222",
                          pushed_by="jdoe@example.com")
    assert parse_push(push(ref="refs/heads/feature/x"), globs) == []
    assert parse_push(push(ref="refs/tags/v1.0"), globs) == []
    assert parse_push(push(after=ZERO), globs) == []  # branch deleted
    [created] = parse_push(push(ref="refs/heads/release/2.0", before=ZERO), globs)
    assert created.branch == "release/2.0" and created.from_hash is None
    no_email = push()
    no_email["pusher"] = {"name": "jdoe"}
    assert parse_push(no_email, globs)[0].pushed_by == "jdoe"


def _commits(shas):
    return [{"sha": s, "commit": {"message": f"msg {s}"}} for s in shas]


@respx.mock
def test_commits_in_push_follows_pages_and_keeps_newest_oldest_first(client):
    nxt = f"{REPO}/compare/c1...c5?per_page=100&page=2"
    route = respx.get(f"{REPO}/compare/c1...c5").mock(side_effect=[
        httpx.Response(200, json={"commits": _commits(["c2", "c3"])}, headers={"Link": f'<{nxt}>; rel="next"'}),
        httpx.Response(200, json={"commits": _commits(["c4", "c5"])}),
    ])
    job = PushJob(repo="acme/payments", branch="main", from_hash="c1", to_hash="c5", pushed_by=None)
    assert client.commits_in_push(job, limit=3) == ["c3", "c4", "c5"]
    assert route.calls[0].request.url.params["per_page"] == "100"
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_new_branch_push_compares_against_default_branch_head(client):
    respx.get(REPO).mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get(f"{REPO}/commits/main").mock(return_value=httpx.Response(200, json={"sha": "m1"}))
    respx.get(f"{REPO}/compare/m1...n2").mock(return_value=httpx.Response(200, json={"commits": _commits(["n1", "n2"])}))
    job = PushJob(repo="acme/payments", branch="release/2", from_hash=None, to_hash="n2", pushed_by=None)
    assert client.commits_in_push(job, limit=50) == ["n1", "n2"]


@respx.mock
def test_recent_commits_newest_limit_oldest_first(client):
    route = respx.get(f"{REPO}/commits").mock(return_value=httpx.Response(200, json=_commits(["c9", "c8", "c7"])))
    assert client.recent_commits("acme/payments", "main", 2) == ["c8", "c9"]
    assert route.calls[0].request.url.params["sha"] == "main"


def _raw(path, ref, content: bytes | None):
    route = respx.get(f"{REPO}/contents/{path}", params={"ref": ref})
    return route.mock(return_value=httpx.Response(404) if content is None else httpx.Response(200, content=content))


@respx.mock
def test_get_commit_maps_metadata_files_and_contents(client):
    respx.get(f"{REPO}/commits/abc").mock(return_value=httpx.Response(200, json={
        "sha": "abc",
        "commit": {"message": "V4-12: Switch flags", "author": {"name": "Jane", "email": "jane@x.io"},
                   "committer": {"date": "2026-11-20T10:30:00Z"}},
        "parents": [{"sha": "par"}],
        "files": [
            {"filename": "src/A.java", "status": "modified"},
            {"filename": "src/B.java", "status": "added"},
            {"filename": "src/C.java", "status": "removed"},
            {"filename": "src/New.java", "status": "renamed", "previous_filename": "src/Old.java"},
            {"filename": "logo.png", "status": "modified"},
        ],
    }))
    _raw("src/A.java", "abc", b"after A")
    _raw("src/A.java", "par", b"before A")
    _raw("src/B.java", "abc", b"new B")
    _raw("src/C.java", "par", b"gone C")
    _raw("src/New.java", "abc", b"renamed after")
    _raw("src/Old.java", "par", b"renamed before")
    _raw("logo.png", "abc", b"\x89PNG\x00\x00")
    _raw("logo.png", "par", b"\x89PNG\x00\x01")
    c = client.get_commit("acme/payments", "abc")
    assert (c.repo, c.sha, c.author_name, c.author_email, c.message, c.parents) == (
        "acme/payments", "abc", "Jane", "jane@x.io", "V4-12: Switch flags", ["par"])
    assert c.committed_at == datetime(2026, 11, 20, 10, 30, tzinfo=timezone.utc)
    got = {f.path: (f.status, f.before, f.after) for f in c.files}
    assert got == {
        "src/A.java": ("modified", "before A", "after A"),
        "src/B.java": ("added", None, "new B"),
        "src/C.java": ("deleted", "gone C", None),
        "src/New.java": ("modified", "renamed before", "renamed after"),
        "logo.png": ("modified", None, None),  # binary: content dropped
    }
    raw_requests = [call.request for call in respx.calls if "/contents/" in call.request.url.path]
    assert all(r.headers["Accept"] == "application/vnd.github.raw+json" for r in raw_requests)


@respx.mock
def test_get_commit_follows_file_pages(client):
    nxt = f"{REPO}/commits/abc?page=2"
    meta = {"sha": "abc", "commit": {"message": "m", "author": {"name": "J", "email": None},
                                     "committer": {"date": "2026-11-20T10:30:00Z"}}, "parents": []}
    respx.get(f"{REPO}/commits/abc").mock(side_effect=[
        httpx.Response(200, json={**meta, "files": [{"filename": "a.txt", "status": "added"}]},
                       headers={"Link": f'<{nxt}>; rel="next"'}),
        httpx.Response(200, json={**meta, "files": [{"filename": "b.txt", "status": "added"}]}),
    ])
    _raw("a.txt", "abc", b"a")
    _raw("b.txt", "abc", b"b")
    assert [f.path for f in client.get_commit("acme/payments", "abc").files] == ["a.txt", "b.txt"]


@respx.mock
def test_get_file_missing_is_none(client):
    _raw("README.md", "v1", None)
    _raw("docs/x.md", "v1", b"hello")
    assert client.get_file("acme/payments", "README.md", "v1") is None
    assert client.get_file("acme/payments", "docs/x.md", "v1") == "hello"


@respx.mock
def test_list_files_uses_recursive_tree_and_globs(client):
    route = respx.get(f"{REPO}/git/trees/v1").mock(return_value=httpx.Response(200, json={"tree": [
        {"path": "src", "type": "tree"},
        {"path": "src/main/resources/application.yml", "type": "blob"},
        {"path": "src/App.java", "type": "blob"},
        {"path": "helm/values.yaml", "type": "blob"},
    ]}))
    assert client.list_files("acme/payments", "v1", ["**/application*.yml", "helm/**"]) == [
        "src/main/resources/application.yml", "helm/values.yaml"]
    assert route.calls[0].request.url.params["recursive"] == "1"
    assert client.list_files("acme/payments", "v1", ["**/*"], limit=1) == ["src/main/resources/application.yml"]


@respx.mock
def test_list_tags_and_commit_messages_between(client):
    respx.get(f"{REPO}/tags").mock(return_value=httpx.Response(200, json=[{"name": "v2.0"}, {"name": "v1.0"}]))
    respx.get(f"{REPO}/compare/v1.0...v2.0").mock(
        return_value=httpx.Response(200, json={"commits": _commits(["o1", "o2", "o3"])}))
    assert client.list_tags("acme/payments") == ["v2.0", "v1.0"]
    assert client.commit_messages_between("acme/payments", "v1.0", "v2.0", limit=2) == ["msg o3", "msg o2"]


@respx.mock
def test_server_errors_raise(client):
    respx.get(f"{REPO}/commits/abc").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        client.get_commit("acme/payments", "abc")
