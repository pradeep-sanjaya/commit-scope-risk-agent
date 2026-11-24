from urllib.parse import quote

import httpx


class NexusClient:
    def __init__(self, http: httpx.Client, maven_repo: str, npm_repo: str):
        self._http = http
        self._maven_repo = maven_repo
        self._npm_repo = npm_repo

    def _get(self, url: str) -> httpx.Response | None:
        response = self._http.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response

    def maven_pom(self, group: str, artifact: str, version: str) -> str | None:
        group_path = "/".join(quote(p, safe="") for p in group.split("."))
        art, ver = quote(artifact, safe=""), quote(version, safe="")
        response = self._get(
            f"/repository/{quote(self._maven_repo, safe='')}/{group_path}/{art}/{ver}/{art}-{ver}.pom"
        )
        return None if response is None else response.text

    def npm_metadata(self, name: str) -> dict | None:
        response = self._get(f"/repository/{quote(self._npm_repo, safe='')}/{quote(name, safe='@')}")
        return None if response is None else response.json()
