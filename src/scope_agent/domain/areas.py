from fnmatch import fnmatchcase

from pydantic import BaseModel, Field


class AreaRule(BaseModel):
    repo: str  # "*", a slug, or "PROJ/slug"
    glob: str
    area: str


class AreaMap(BaseModel):
    rules: list[AreaRule] = Field(default_factory=list)

    def area_for(self, repo: str, path: str) -> str:
        slug = repo.rsplit("/", 1)[-1]
        for rule in self.rules:
            if rule.repo not in ("*", slug, repo):
                continue
            if fnmatchcase(path, rule.glob):
                return rule.area
        first, sep, _ = path.partition("/")
        return f"{slug}: {first if sep else '(root)'}"
