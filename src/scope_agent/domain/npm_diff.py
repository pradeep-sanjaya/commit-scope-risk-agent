"""Diff npm package.json / package-lock.json contents (pure)."""

import json

from scope_agent.domain.models import DependencyChange
from scope_agent.domain.versions import change_kind, is_major_bump

_DEP_SECTIONS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
_PREFIX = "node_modules/"


def _load(text: str | None) -> dict:
    if not text or not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _declared(data: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for section in _DEP_SECTIONS:
        deps = data.get(section)
        if isinstance(deps, dict):
            out.update({k: str(v) for k, v in deps.items()})
    return out


def _strip(range_: str) -> str:
    return range_.strip().lstrip("^~>=< ").strip()


def _package_json(path: str, before: str | None, after: str | None) -> list[DependencyChange]:
    old, new = _declared(_load(before)), _declared(_load(after))
    out: list[DependencyChange] = []

    def add(name: str, o: str | None, n: str | None, kind: str) -> None:
        out.append(DependencyChange(ecosystem="npm", name=name, old_version=o, new_version=n, kind=kind, file=path))

    for name in sorted(old.keys() | new.keys()):
        o = _strip(old[name]) if name in old else None
        n = _strip(new[name]) if name in new else None
        if o is None:
            add(name, None, n, "added")
        elif n is None:
            add(name, o, None, "removed")
        else:
            kind = change_kind(o, n)
            if kind:
                add(name, o, n, kind)
    return out


def _installed(data: dict) -> dict[str, str]:
    """Top-level installed packages: name -> version (lockfile v2/v3)."""
    pkgs = data.get("packages")
    out: dict[str, str] = {}
    if isinstance(pkgs, dict):
        for key, meta in pkgs.items():
            if key.startswith(_PREFIX) and _PREFIX not in key[len(_PREFIX):] and isinstance(meta, dict):
                if isinstance(meta.get("version"), str):
                    out[key[len(_PREFIX):]] = meta["version"]
    return out


def _lockfile(path: str, before: str | None, after: str | None) -> list[DependencyChange]:
    b, a = _load(before), _load(after)
    root = (a.get("packages") or {}).get("") if isinstance(a.get("packages"), dict) else None
    direct = set(_declared(root)) if isinstance(root, dict) else set()
    root_b = (b.get("packages") or {}).get("") if isinstance(b.get("packages"), dict) else None
    if isinstance(root_b, dict):
        direct |= set(_declared(root_b))
    old, new = _installed(b), _installed(a)
    return [
        DependencyChange(ecosystem="npm", name=name, old_version=old[name], new_version=new[name], kind="transitive", file=path)
        for name in sorted(old.keys() & new.keys())
        if name not in direct and old[name] != new[name] and is_major_bump(old[name], new[name])
    ]


def npm_changes(path: str, before: str | None, after: str | None) -> list[DependencyChange]:
    try:
        if path.rsplit("/", 1)[-1] == "package-lock.json":
            return _lockfile(path, before, after)
        return _package_json(path, before, after)
    except (ValueError, TypeError):  # json.JSONDecodeError is a ValueError
        return []
