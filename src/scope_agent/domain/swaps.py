"""Detect one library being replaced by another in the same push. Pure."""

import difflib
import re
from typing import Literal

from pydantic import BaseModel

from scope_agent.domain.models import CommitData, DependencyChange

GENERIC_TOKENS = frozenset(
    "com org io dev net java js core client lib util utils api sdk contrib providers "
    "provider starter common web corp".split()
)
_SPLIT = re.compile(r"[-.:_/@]")
_QUOTED = re.compile(r"""['"]([^'"]+)['"]""")


class Swap(BaseModel):
    removed: DependencyChange
    added: DependencyChange
    reason: Literal["configured", "name_overlap", "import_change"]


def import_prefixes(change: DependencyChange) -> list[str]:
    if change.ecosystem == "npm":
        return [change.name]
    group, _, artifact = change.name.partition(":")
    prefixes = [group]
    if artifact:
        prefixes.append(f"{group}.{artifact.replace('-', '')}")
    return prefixes


def _tokens(name: str) -> set[str]:
    return {t for t in _SPLIT.split(name.lower()) if len(t) >= 3 and t not in GENERIC_TOKENS}


def _changed_lines(before: str | None, after: str | None) -> tuple[list[str], list[str]]:
    b = (before or "").splitlines()
    a = (after or "").splitlines()
    removed: list[str] = []
    added: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed.extend(b[i1:i2])
        if op in ("replace", "insert"):
            added.extend(a[j1:j2])
    return removed, added


def _imports(line: str, prefix: str) -> bool:
    s = line.strip()
    if not (s.startswith("import ") or "from '" in s or 'from "' in s):
        return False
    rest = s[len("import "):].strip() if s.startswith("import ") else ""
    if rest.startswith("static "):
        rest = rest[len("static "):].strip()
    rest = rest.rstrip(";").strip()
    if rest == prefix or rest.startswith(prefix + "."):
        return True
    return any(m == prefix or m.startswith(prefix + "/") for m in _QUOTED.findall(s))


def _any_import(lines: list[str], change: DependencyChange) -> bool:
    return any(_imports(ln, p) for ln in lines for p in import_prefixes(change))


def _import_change(removed: DependencyChange, added: DependencyChange, commits: list[CommitData]) -> bool:
    for commit in commits:
        for f in commit.files:
            gone, new = _changed_lines(f.before, f.after)
            if _any_import(gone, removed) and _any_import(new, added):
                return True
    return False


def detect_swaps(
    changes: list[DependencyChange],
    commits: list[CommitData],
    known_swaps: list[tuple[str, str]],
) -> list[Swap]:
    removed = [c for c in changes if c.kind == "removed"]
    added = [c for c in changes if c.kind == "added"]
    known = {tuple(p) for p in known_swaps}
    rules = (
        ("configured", lambda r, a: (r.name, a.name) in known),
        ("name_overlap", lambda r, a: bool(_tokens(r.name) & _tokens(a.name))),
        ("import_change", lambda r, a: _import_change(r, a, commits)),
    )
    swaps: list[Swap] = []
    used: set[int] = set()
    for r in removed:
        found = False
        for reason, matches in rules:
            for i, a in enumerate(added):
                if i in used or a.ecosystem != r.ecosystem:
                    continue
                if matches(r, a):
                    swaps.append(Swap(removed=r, added=a, reason=reason))
                    used.add(i)
                    found = True
                    break
            if found:
                break
    return swaps
