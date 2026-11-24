from pydantic import BaseModel, Field, field_validator


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
