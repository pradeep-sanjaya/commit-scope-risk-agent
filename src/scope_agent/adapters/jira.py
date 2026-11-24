from urllib.parse import quote

import httpx

BASE_FIELDS = ["summary", "description", "issuetype", "status", "components", "labels", "parent", "issuelinks"]


class JiraClient:
    def __init__(self, http: httpx.Client):
        self._http = http

    def get_issue(self, key: str, extra_fields: list[str]) -> dict | None:
        response = self._http.get(
            f"/rest/api/3/issue/{quote(key, safe='')}",
            params={"fields": ",".join([*BASE_FIELDS, *extra_fields])},
        )
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    def search(self, jql: str, fields: list[str], max_results: int = 200) -> list[dict]:
        issues: list[dict] = []
        token: str | None = None
        while len(issues) < max_results:
            body: dict = {"jql": jql, "fields": fields, "maxResults": max_results - len(issues)}
            if token:
                body["nextPageToken"] = token
            response = self._http.post("/rest/api/3/search/jql", json=body)
            response.raise_for_status()
            page = response.json()
            issues.extend(page.get("issues", []))
            token = page.get("nextPageToken")
            if not token or page.get("isLast"):
                break
        return issues[:max_results]
