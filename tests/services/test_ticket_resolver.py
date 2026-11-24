import json
from pathlib import Path

import httpx
import pytest
import respx

from scope_agent.adapters.jira import JiraClient
from scope_agent.persistence.cache import KeyValueCache
from scope_agent.persistence.database import Database
from scope_agent.services.ticket_resolver import TicketResolver
from tests.persistence.conftest import FakeClock

BASE = "https://jira.test"
FX = Path(__file__).parent.parent / "fixtures"


def load(name):
    return json.loads((FX / name).read_text())


@pytest.fixture
def cache():
    db = Database("sqlite://")
    db.create_all()
    return KeyValueCache(db, FakeClock())


def make(cache, ac_field=None):
    return TicketResolver(JiraClient(httpx.Client(base_url=BASE)), cache, ac_field, "Acceptance Criteria", 600)


def issue(key):
    return respx.get(f"{BASE}/rest/api/3/issue/{key}")


@respx.mock
def test_resolver_epic_direct_parent(cache):
    issue("PAY-12").respond(200, json=load("jira_issue.json"))
    issue("PAY-10").respond(200, json=load("jira_epic.json"))
    t = make(cache).resolve("PAY-12")
    assert t.key == "PAY-12" and t.summary == "Add flag client" and t.issue_type == "Story"
    assert t.status == "In Progress" and t.components == ["flags"] and t.labels == ["backend"]
    assert t.epic_key == "PAY-10" and t.epic_summary == "Feature flags"
    assert t.epic_description == "Epic goal text"
    assert t.acceptance_criteria == "- flags load on startup\n- falls back to defaults"
    assert t.linked_titles == ["Config loader", "Rollout plan"]


@respx.mock
def test_resolver_subtask_walks_to_epic(cache):
    issue("PAY-13").respond(200, json=load("jira_story_parent.json"))
    issue("PAY-12").respond(200, json=load("jira_issue.json"))
    issue("PAY-10").respond(200, json=load("jira_epic.json"))
    t = make(cache).resolve("PAY-13")
    assert t.epic_key == "PAY-10" and t.epic_summary == "Feature flags"
    assert t.description == "" and t.acceptance_criteria is None


@respx.mock
def test_resolver_no_epic_when_chain_too_long(cache):
    def story(key, parent):
        return {"key": key, "fields": {"summary": key, "issuetype": {"name": "Story"}, "status": {"name": "x"},
                "parent": {"key": parent}}}
    issue("A-1").respond(200, json=story("A-1", "A-2"))
    issue("A-2").respond(200, json=story("A-2", "A-3"))
    issue("A-3").respond(200, json=story("A-3", "A-4"))
    t = make(cache).resolve("A-1")
    assert t.epic_key is None and t.epic_summary is None


@respx.mock
def test_resolver_returns_none_for_missing(cache):
    issue("PAY-9").respond(404)
    assert make(cache).resolve("PAY-9") is None


@respx.mock
def test_resolver_uses_ac_field(cache):
    issue("PAY-12").respond(200, json=load("jira_issue.json"))
    issue("PAY-10").respond(200, json=load("jira_epic.json"))
    assert make(cache, "customfield_10050").resolve("PAY-12").acceptance_criteria == "custom AC"


@respx.mock
def test_resolver_ac_field_plain_string(cache):
    data = load("jira_issue.json")
    data["fields"]["customfield_10050"] = "plain ac"
    issue("PAY-12").respond(200, json=data)
    issue("PAY-10").respond(200, json=load("jira_epic.json"))
    assert make(cache, "customfield_10050").resolve("PAY-12").acceptance_criteria == "plain ac"


@respx.mock
def test_resolver_caches(cache):
    r12 = issue("PAY-12").respond(200, json=load("jira_issue.json"))
    issue("PAY-10").respond(200, json=load("jira_epic.json"))
    r = make(cache)
    first = r.resolve("PAY-12")
    calls = respx.calls.call_count
    assert r.resolve("PAY-12") == first
    assert respx.calls.call_count == calls and r12.call_count == 1


@respx.mock
def test_resolver_caches_missing(cache):
    route = issue("PAY-9").respond(404)
    r = make(cache)
    assert r.resolve("PAY-9") is None and r.resolve("PAY-9") is None
    assert route.call_count == 1


@respx.mock
def test_epic_tickets_jql_and_pagination(cache):
    def row(k, assignee):
        return {"key": k, "fields": {"summary": f"s{k}", "status": {"name": "Done"}, "assignee": assignee}}
    route = respx.post(f"{BASE}/rest/api/3/search/jql").mock(side_effect=[
        httpx.Response(200, json={"issues": [row("PAY-1", {"displayName": "Ann"})], "nextPageToken": "n"}),
        httpx.Response(200, json={"issues": [row("PAY-2", None)], "isLast": True}),
    ])
    r = make(cache)
    tickets = r.epic_tickets("PAY-10")
    assert [(t.key, t.summary, t.status, t.assignee) for t in tickets] == [
        ("PAY-1", "sPAY-1", "Done", "Ann"), ("PAY-2", "sPAY-2", "Done", None)]
    assert json.loads(route.calls[0].request.content)["jql"] == "parent = PAY-10 ORDER BY key"
    assert r.epic_tickets("PAY-10") == tickets
    assert route.call_count == 2
