import re

_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _find(text: str, projects: set[str]) -> list[str]:
    found: list[str] = []
    for key in _KEY_RE.findall(text):
        if key.rsplit("-", 1)[0] in projects and key not in found:
            found.append(key)
    return found


def extract_keys(message: str, branch: str | None, project_keys: list[str]) -> list[str]:
    """Ordered, unique JIRA keys of configured projects; the branch is a fallback only."""
    projects = set(project_keys)
    keys = _find(message, projects)
    if not keys and branch:
        keys = _find(branch, projects)
    return keys
