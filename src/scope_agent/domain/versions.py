"""Pure version parsing and comparison helpers (no I/O)."""

import re

_SPLIT = re.compile(r"[.\-+]")
_RELEASE_QUALIFIERS = {"final", "ga", "release"}

_UNPARSEABLE: tuple = (0, (), 0, ())


def _split(v: str) -> tuple[list[int], list[str]]:
    """Leading numeric components and the remaining (qualifier) parts."""
    parts = [p for p in _SPLIT.split(v.strip()) if p != ""]
    nums: list[int] = []
    i = 0
    while i < len(parts) and parts[i].isascii() and parts[i].isdigit():
        nums.append(int(parts[i]))
        i += 1
    return nums, parts[i:]


def _qual_part(p: str) -> tuple[int, int, str]:
    return (0, int(p), "") if p.isascii() and p.isdigit() else (1, 0, p.lower())


def version_key(v: str) -> tuple:
    """Totally ordered key. Pre-release qualifiers sort before the release;
    unparseable input sorts lowest. Never raises."""
    try:
        nums, rest = _split(v)
    except Exception:  # defensive: non-str input
        return _UNPARSEABLE
    if not nums:
        return _UNPARSEABLE
    while len(nums) > 1 and nums[-1] == 0:
        nums.pop()
    quals = [q for q in rest if q.lower() not in _RELEASE_QUALIFIERS]
    prerelease = 0 if quals else 1
    return (1, tuple(nums), prerelease, tuple(_qual_part(q) for q in quals))


def is_parseable(v: str) -> bool:
    return version_key(v) != _UNPARSEABLE


def is_major_bump(old: str, new: str) -> bool:
    """First numeric component changed, or the second when the version is 0.x."""
    o, _ = _split(old)
    n, _ = _split(new)
    if not o or not n:
        return False
    if o[0] != n[0]:
        return True
    if o[0] == 0:
        return (o[1] if len(o) > 1 else 0) != (n[1] if len(n) > 1 else 0)
    return False


def versions_between(all_versions: list[str], old: str, new: str) -> list[str]:
    """Versions v with old < v <= new, ascending; unparseable entries skipped."""
    lo, hi = version_key(old), version_key(new)
    found = [v for v in all_versions if is_parseable(v) and lo < version_key(v) <= hi]
    return sorted(found, key=version_key)


def change_kind(old: str, new: str) -> str | None:
    """'upgraded' / 'downgraded', or None when the versions are equal.

    Unparseable versions (e.g. unresolved ``${x}``) count as upgraded if the
    raw strings differ.
    """
    if old == new:
        return None
    if not (is_parseable(old) and is_parseable(new)):
        return "upgraded"
    ko, kn = version_key(old), version_key(new)
    if ko == kn:
        return None
    return "upgraded" if kn > ko else "downgraded"
