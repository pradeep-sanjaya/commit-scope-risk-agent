import hashlib
import hmac
import json
from datetime import timezone
from pathlib import Path

import httpx
import pytest
import respx

from scope_agent.adapters.bitbucket import BitbucketClient, parse_refs_changed, verify_signature
from scope_agent.domain.models import PushJob

BASE = "https://bb.test"
API = f"{BASE}/rest/api/1.0/projects/PAY/repos/svc"
FIXTURE = Path(__file__).parent.parent / "fixtures" / "refs_changed.json"


@pytest.fixture
def client():
    return BitbucketClient(httpx.Client(base_url=BASE))


def test_verify_signature_ok_bad_missing():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s3", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3", body, good)
    assert not verify_signature("s3", body, "sha256=deadbeef")
    assert not verify_signature("other", body, good)
    assert not verify_signature("s3", body, None)
    assert not verify_signature("s3", body, "")


def test_parse_filters_branches_tags_deletes():
    payload = json.loads(FIXTURE.read_text())
    jobs = parse_refs_changed(payload, ["development", "hotfix/*", "release/*"])
    assert [(j.repo, j.branch) for j in jobs] == [
        ("PAY/payments-service", "development"),
        ("PAY/payments-service", "release/1.4"),
    ]
    assert jobs[0].from_hash == "aaa111" and jobs[0].to_hash == "bbb222"
    assert jobs[1].from_hash is None
    assert jobs[0].pushed_by == "jdoe@example.com"


def _commits_page(shas, last, nxt=None):
    body = {"values": [{"id": s} for s in shas], "isLastPage": last}
    if nxt is not None:
        body["nextPageStart"] = nxt
    return httpx.Response(200, json=body)


@respx.mock
def test_commits_in_push_paginates_oldest_first(client):
    route = respx.get(f"{API}/commits").mock(
        side_effect=[_commits_page(["c5", "c4"], False, 2), _commits_page(["c3", "c2"], True)]
    )
    job = PushJob(repo="PAY/svc", branch="development", from_hash="c1", to_hash="c5", pushed_by=None)
    assert client.commits_in_push(job, limit=3) == ["c3", "c4", "c5"]
    first = route.calls[0].request.url.params
    assert first["until"] == "c5" and first["since"] == "c1"
    assert route.calls[1].request.url.params["start"] == "2"


@respx.mock
def test_commits_in_push_new_branch_uses_default_head(client):
    respx.get(f"{API}/branches/default").mock(
        return_value=httpx.Response(200, json={"latestCommit": "dh"})
    )
    route = respx.get(f"{API}/commits").mock(return_value=_commits_page(["c2", "c1"], True))
    job = PushJob(repo="PAY/svc", branch="release/1.4", from_hash=None, to_hash="c2", pushed_by=None)
    assert client.commits_in_push(job, limit=10) == ["c1", "c2"]
    assert route.calls[0].request.url.params["since"] == "dh"


def _mock_commit(sha, parents, changes):
    respx.get(f"{API}/commits/{sha}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": sha,
                "message": "PAY-1 do thing",
                "author": {"name": "Jane", "emailAddress": "jane@example.com"},
                "committerTimestamp": 1700000000000,
                "parents": [{"id": p} for p in parents],
            },
        )
    )
    respx.get(f"{API}/commits/{sha}/changes").mock(
        return_value=httpx.Response(200, json={"values": changes, "isLastPage": True})
    )


def _chg(path, typ, src=None):
    d = {"path": {"toString": path}, "type": typ}
    if src:
        d["srcPath"] = {"toString": src}
    return d


@respx.mock
def test_get_commit_builds_file_changes(client):
    _mock_commit(
        "s2", ["s1"],
        [_chg("a.txt", "ADD"), _chg("b.txt", "DELETE"), _chg("c.txt", "MODIFY"), _chg("n.txt", "MOVE", "o.txt")],
    )
    raw = lambda path, at, resp: respx.get(f"{API}/raw/{path}", params={"at": at}).mock(return_value=resp)
    raw("a.txt", "s2", httpx.Response(200, text="new"))
    raw("a.txt", "s1", httpx.Response(404))
    raw("b.txt", "s2", httpx.Response(404))
    raw("b.txt", "s1", httpx.Response(200, text="old"))
    raw("c.txt", "s2", httpx.Response(200, text="after"))
    raw("c.txt", "s1", httpx.Response(200, text="before"))
    raw("n.txt", "s2", httpx.Response(200, text="moved"))
    raw("o.txt", "s1", httpx.Response(200, text="orig"))
    c = client.get_commit("PAY/svc", "s2")
    assert c.author_name == "Jane" and c.author_email == "jane@example.com"
    assert c.parents == ["s1"] and c.message == "PAY-1 do thing"
    assert c.committed_at.tzinfo == timezone.utc and c.committed_at.year == 2023
    by = {f.path: f for f in c.files}
    assert (by["a.txt"].status, by["a.txt"].before, by["a.txt"].after) == ("added", None, "new")
    assert (by["b.txt"].status, by["b.txt"].before, by["b.txt"].after) == ("deleted", "old", None)
    assert (by["c.txt"].status, by["c.txt"].before, by["c.txt"].after) == ("modified", "before", "after")
    assert (by["n.txt"].status, by["n.txt"].before, by["n.txt"].after) == ("modified", "orig", "moved")


@respx.mock
def test_get_commit_marks_binary_and_oversize(client):
    _mock_commit("s2", ["s1"], [_chg("img.png", "MODIFY"), _chg("big.txt", "MODIFY"), _chg("ok.txt", "MODIFY")])
    raw = lambda path, at, resp: respx.get(f"{API}/raw/{path}", params={"at": at}).mock(return_value=resp)
    raw("img.png", "s2", httpx.Response(200, content=b"\x89PNG\x00\x01"))
    raw("img.png", "s1", httpx.Response(200, content=b"\x89PNG\x00\x00"))
    raw("big.txt", "s2", httpx.Response(200, content=b"x" * (1024 * 1024 + 1)))
    raw("big.txt", "s1", httpx.Response(200, text="small"))
    raw("ok.txt", "s2", httpx.Response(200, text="a"))
    raw("ok.txt", "s1", httpx.Response(200, text="b"))
    c = client.get_commit("PAY/svc", "s2")
    by = {f.path: f for f in c.files}
    for p in ("img.png", "big.txt"):
        assert by[p].before is None and by[p].after is None
    assert by["ok.txt"].after == "a"


@respx.mock
def test_get_file_404_is_none(client):
    respx.get(f"{API}/raw/x/y.txt", params={"at": "main"}).mock(return_value=httpx.Response(404))
    assert client.get_file("PAY/svc", "x/y.txt", "main") is None


@respx.mock
def test_list_files_filters_globs_and_limits(client):
    respx.get(f"{API}/files").mock(
        side_effect=[
            httpx.Response(200, json={"values": ["pom.xml", "a/pom.xml", "README.md"], "isLastPage": False, "nextPageStart": 3}),
            httpx.Response(200, json={"values": ["b/package.json", "c/pom.xml"], "isLastPage": True}),
        ]
    )
    assert client.list_files("PAY/svc", "main", ["pom.xml", "*/pom.xml"], limit=2) == ["pom.xml", "a/pom.xml"]


@respx.mock
def test_default_head_tags_and_messages(client):
    respx.get(f"{API}/branches/default").mock(return_value=httpx.Response(200, json={"latestCommit": "dh"}))
    respx.get(f"{API}/tags").mock(
        return_value=httpx.Response(200, json={"values": [{"displayId": "v1"}, {"displayId": "v2"}], "isLastPage": True})
    )
    route = respx.get(f"{API}/commits").mock(
        return_value=httpx.Response(200, json={"values": [{"message": "m1"}, {"message": "m2"}], "isLastPage": True})
    )
    assert client.default_branch_head("PAY/svc") == "dh"
    assert client.list_tags("PAY/svc") == ["v1", "v2"]
    assert client.commit_messages_between("PAY/svc", "v1", "v2") == ["m1", "m2"]
    p = route.calls[0].request.url.params
    assert p["since"] == "v1" and p["until"] == "v2"


@respx.mock
def test_get_file_encodes_special_path_characters(client):
    route = respx.get(host="bb.test").mock(return_value=httpx.Response(200, text="hi"))
    assert client.get_file("PAY/svc", "docs/a b#c?d%41.md", "main") == "hi"
    req = route.calls[0].request
    assert req.url.raw_path == (
        b"/rest/api/1.0/projects/PAY/repos/svc/raw/docs/a%20b%23c%3Fd%2541.md?at=main"
    )
