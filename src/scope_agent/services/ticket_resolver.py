import json

import httpx
import structlog

from scope_agent.adapters.jira import JiraClient
from scope_agent.domain.adf import adf_to_text, extract_ac
from scope_agent.domain.models import EpicTicket, TicketContext
from scope_agent.persistence.cache import KeyValueCache

NAMESPACE = "jira"
MAX_EPIC_HOPS = 2
_MISSING = "null"

log = structlog.get_logger()


def _is_epic(issue: dict) -> bool:
    issue_type = issue.get("fields", {}).get("issuetype") or {}
    return issue_type.get("name") == "Epic" or issue_type.get("hierarchyLevel") == 1


def _text(value: dict | str | None) -> str:
    return value if isinstance(value, str) else adf_to_text(value)


class TicketResolver:
    def __init__(self, jira: JiraClient, cache: KeyValueCache, ac_field: str | None,
                 ac_heading: str, cache_ttl: int):
        self._jira = jira
        self._cache = cache
        self._ac_field = ac_field
        self._ac_heading = ac_heading
        self._ttl = cache_ttl

    def resolve(self, key: str) -> TicketContext | None:
        cached = self._cache.get(NAMESPACE, key)
        if cached is not None:
            return None if cached == _MISSING else TicketContext.model_validate_json(cached)
        ticket = self._build(key)
        self._cache.put(NAMESPACE, key, _MISSING if ticket is None else ticket.model_dump_json(), self._ttl)
        return ticket

    def _build(self, key: str) -> TicketContext | None:
        extra = [self._ac_field] if self._ac_field else []
        issue = self._jira.get_issue(key, extra)
        if issue is None:
            return None
        fields = issue["fields"]
        description = _text(fields.get("description"))
        epic = self._find_epic(issue)
        epic_fields = epic["fields"] if epic else {}
        return TicketContext(
            key=issue["key"],
            summary=fields.get("summary", ""),
            description=description,
            issue_type=(fields.get("issuetype") or {}).get("name", ""),
            status=(fields.get("status") or {}).get("name", ""),
            acceptance_criteria=self._acceptance_criteria(fields, description),
            components=[c["name"] for c in fields.get("components") or []],
            labels=list(fields.get("labels") or []),
            epic_key=epic["key"] if epic else None,
            epic_summary=epic_fields.get("summary") if epic else None,
            epic_description=_text(epic_fields.get("description")) if epic else None,
            linked_titles=self._linked_titles(fields),
        )

    def _acceptance_criteria(self, fields: dict, description: str) -> str | None:
        if self._ac_field:
            return _text(fields.get(self._ac_field)).strip() or None
        return extract_ac(description, self._ac_heading)

    @staticmethod
    def _linked_titles(fields: dict) -> list[str]:
        titles: list[str] = []
        for link in fields.get("issuelinks") or []:
            other = link.get("outwardIssue") or link.get("inwardIssue") or {}
            title = other.get("fields", {}).get("summary")
            if title:
                titles.append(title)
        return titles

    def _find_epic(self, issue: dict) -> dict | None:
        current = issue
        for _ in range(MAX_EPIC_HOPS):
            parent = (current["fields"].get("parent") or {}).get("key")
            if not parent:
                return None
            current = self._jira.get_issue(parent, [])
            if current is None:
                return None
            if _is_epic(current):
                return current
        return None

    def epic_tickets(self, epic_key: str) -> list[EpicTicket]:
        cache_key = f"epic_tickets:{epic_key}"
        cached = self._cache.get(NAMESPACE, cache_key)
        if cached is not None:
            return [EpicTicket(**row) for row in json.loads(cached)]
        try:
            issues = self._jira.search(f"parent = {epic_key} ORDER BY key", ["summary", "status", "assignee"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            # JIRA answers 400 when the key does not exist or is not visible to us: it has no children.
            log.warning("jira_epic_tickets_unknown_key", key=epic_key)
            issues = []
        tickets = [
            EpicTicket(
                key=i["key"],
                summary=i["fields"].get("summary", ""),
                status=(i["fields"].get("status") or {}).get("name", ""),
                assignee=(i["fields"].get("assignee") or {}).get("displayName"),
            )
            for i in issues
        ]
        self._cache.put(NAMESPACE, cache_key, json.dumps([t.model_dump() for t in tickets]), self._ttl)
        return tickets
