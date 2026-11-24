import hashlib
import hmac
from urllib.parse import quote


def rating_token(secret: str, finding_id: str, value: str) -> str:
    digest = hmac.new(secret.encode(), f"{finding_id}:{value}".encode(), hashlib.sha256).hexdigest()
    return digest[:32]


def verify_rating_token(secret: str, finding_id: str, value: str, token: str) -> bool:
    expected = rating_token(secret, finding_id, value)
    return hmac.compare_digest(expected.encode(), token.encode("utf-8", "replace"))


def rating_url(base: str, secret: str, finding_id: str, value: str) -> str:
    token = rating_token(secret, finding_id, value)
    return f"{base.rstrip('/')}/rate/{finding_id}?v={value}&t={token}"


class LinkBuilder:
    def __init__(self, source_url: str, jira_url: str, public_base_url: str, rating_secret: str,
                 source_provider: str = "bitbucket"):
        """`source_url` is the web base of the source host: Bitbucket's URL, or https://github.com."""
        self._source = source_url.rstrip("/")
        self._provider = source_provider
        self._jira = jira_url.rstrip("/")
        self._public = public_base_url.rstrip("/")
        self._secret = rating_secret

    def commit(self, repo: str, sha: str) -> str:
        owner, name = repo.split("/", 1)
        if self._provider == "github":
            return f"{self._source}/{owner}/{name}/commit/{sha}"
        return f"{self._source}/projects/{owner}/repos/{name}/commits/{sha}"

    def ticket(self, key: str) -> str:
        return f"{self._jira}/browse/{key}"

    def rating(self, finding_id: str, value: str) -> str:
        return rating_url(self._public, self._secret, finding_id, value)

    def portal_epic(self, subject: str) -> str:
        return f"{self._public}/portal/epics/{quote(subject, safe='')}"

    def portal_ticket(self, key: str) -> str:
        return f"{self._public}/portal/tickets/{quote(key, safe='')}"
