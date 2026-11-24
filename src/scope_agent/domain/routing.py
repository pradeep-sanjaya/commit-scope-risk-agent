import fnmatch

from pydantic import BaseModel, Field, field_validator

from scope_agent.domain.models import CommitData, Finding


class RepoRouting(BaseModel):
    leads: list[str] = Field(default_factory=list)
    paths: dict[str, list[str]] = Field(default_factory=dict)


class RoutingConfig(BaseModel):
    repos: dict[str, RepoRouting] = Field(default_factory=dict)
    categories: dict[str, list[str]] = Field(default_factory=dict)
    default_leads: list[str]

    @field_validator("default_leads")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("default_leads must not be empty")
        return v


def _dedupe_lower(addresses: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for a in addresses:
        seen.setdefault(a.lower(), None)
    return list(seen)


def reviewers_for(finding: Finding, routing: RoutingConfig) -> list[str]:
    """Repo leads, path owners, then category owners; default leads as fallback."""
    slug = finding.repo.rsplit("/", 1)[-1]
    repo = routing.repos.get(finding.repo) or routing.repos.get(slug)
    leads = list(repo.leads) if repo else []
    path_owners: list[str] = []
    if repo:
        for pattern, owners in repo.paths.items():
            if any(fnmatch.fnmatchcase(f, pattern) for f in finding.files):
                path_owners.extend(owners)
    category_owners = routing.categories.get(finding.category, [])
    out = leads + path_owners
    if not out:
        out = list(routing.default_leads)
    return _dedupe_lower(out + list(category_owners))


def push_recipients(
    findings: list[Finding], commits: list[CommitData], routing: RoutingConfig
) -> list[str]:
    """Authors of flagged commits first, then the union of reviewers."""
    flagged = {f.commit for f in findings}
    addresses = [
        c.author_email for c in commits if c.sha in flagged and c.author_email
    ]
    for f in findings:
        addresses.extend(reviewers_for(f, routing))
    return _dedupe_lower(addresses)
