from pydantic import BaseModel, Field


class DependencyHints(BaseModel):
    library_docs: dict[str, list[str]] = Field(default_factory=dict)
    internal_repos: dict[str, str] = Field(default_factory=dict)  # library -> "PROJ/slug"
    known_swaps: list[tuple[str, str]] = Field(default_factory=list)
