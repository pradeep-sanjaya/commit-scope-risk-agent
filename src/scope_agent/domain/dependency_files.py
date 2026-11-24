"""Dispatch a file change to the right dependency differ, by basename."""

from scope_agent.domain.maven_diff import maven_changes
from scope_agent.domain.models import DependencyChange, FileChange
from scope_agent.domain.npm_diff import npm_changes


def dependency_changes(fc: FileChange) -> list[DependencyChange]:
    base = fc.path.rsplit("/", 1)[-1]
    if base == "pom.xml":
        return maven_changes(fc.path, fc.before, fc.after)
    if base in ("package.json", "package-lock.json"):
        return npm_changes(fc.path, fc.before, fc.after)
    return []
