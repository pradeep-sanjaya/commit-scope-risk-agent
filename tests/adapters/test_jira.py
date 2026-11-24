import json

import httpx
import pytest
import respx

from scope_agent.adapters.jira import JiraClient

BASE = "https://jira.test"


@pytest.fixture
def client():
    return JiraClient(httpx.Client(base_url=BASE))


@respx.mock
def test_jira_client_returns_none_on_404(client):
    respx.get(f"{BASE}/rest/api/3/issue/PAY-404").respond(404)
    respx.get(f"{BASE}/rest/api/3/issue/PAY-403").respond(403)
    assert client.get_issue("PAY-404", []) is None
    assert client.get_issue("PAY-403", []) is None


@respx.mock
def test_get_issue_fields_and_error(client):
    route = respx.get(f"{BASE}/rest/api/3/issue/PAY-1").respond(200, json={"key": "PAY-1"})
    assert client.get_issue("PAY-1", ["customfield_1"]) == {"key": "PAY-1"}
    fields = route.calls[0].request.url.params["fields"]
    assert fields == "summary,description,issuetype,status,components,labels,parent,issuelinks,customfield_1"
    respx.get(f"{BASE}/rest/api/3/issue/PAY-2").respond(500)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_issue("PAY-2", [])


@respx.mock
def test_search_follows_next_page_token_and_caps(client):
    route = respx.post(f"{BASE}/rest/api/3/search/jql").mock(side_effect=[
        httpx.Response(200, json={"issues": [{"key": "A-1"}, {"key": "A-2"}], "nextPageToken": "t2"}),
        httpx.Response(200, json={"issues": [{"key": "A-3"}], "isLast": True}),
    ])
    assert [i["key"] for i in client.search("parent = A-0", ["summary"])] == ["A-1", "A-2", "A-3"]
    assert "nextPageToken" not in json.loads(route.calls[0].request.content)
    assert json.loads(route.calls[1].request.content)["nextPageToken"] == "t2"

    respx.post(f"{BASE}/rest/api/3/search/jql").respond(
        200, json={"issues": [{"key": "A-1"}, {"key": "A-2"}], "nextPageToken": "z"})
    assert len(client.search("q", ["summary"], max_results=1)) == 1
