from urllib.parse import quote

import httpx

PAGE_SIZE = 100
_RAW = {"Accept": "application/vnd.github.raw+json"}
_SOFT_FAIL = (403, 404, 429)


class GitHubClient:
    def __init__(self, http: httpx.Client):
        self._http = http

    @staticmethod
    def _base(owner_repo: str) -> str:
        owner, name = owner_repo.split("/", 1)
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    def _raw(self, url: str) -> str | None:
        response = self._http.get(url, headers=_RAW)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.text

    def releases(self, owner_repo: str, max_pages: int = 5) -> list[dict] | None:
        found: list[dict] = []
        for page in range(1, max_pages + 1):
            response = self._http.get(
                f"{self._base(owner_repo)}/releases", params={"per_page": PAGE_SIZE, "page": page}
            )
            if response.status_code in _SOFT_FAIL:
                return None
            response.raise_for_status()
            batch = response.json()
            found.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
        return found

    def file(self, owner_repo: str, path: str) -> str | None:
        return self._raw(f"{self._base(owner_repo)}/contents/{quote(path, safe='/')}")

    def readme(self, owner_repo: str, subpath: str | None) -> str | None:
        suffix = f"/{quote(subpath, safe='/')}" if subpath else ""
        return self._raw(f"{self._base(owner_repo)}/readme{suffix}")
