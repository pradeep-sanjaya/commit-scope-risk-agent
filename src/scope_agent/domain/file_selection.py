import difflib
from fnmatch import fnmatchcase

from scope_agent.domain.models import FileChange

MAX_FILE_CHARS = 1_000_000  # larger files are not sent for review


def unified_diff(fc: FileChange) -> str:
    lines = difflib.unified_diff(
        (fc.before or "").splitlines(), (fc.after or "").splitlines(),
        fromfile=f"a/{fc.path}", tofile=f"b/{fc.path}", n=3, lineterm="",
    )
    return "\n".join(lines)


def _matches(path: str, globs: list[str]) -> bool:
    for g in globs:
        if fnmatchcase(path, g):
            return True
        if g.startswith("**/") and fnmatchcase(path, g[3:]):  # "**/" also matches the repo root
            return True
    return False


def _unreviewable(fc: FileChange) -> bool:
    if fc.before == fc.after:
        return True
    for text in (fc.before, fc.after):
        if text and ("\x00" in text or len(text) > MAX_FILE_CHARS):
            return True
    return False


def select_files(files: list[FileChange], exclude: list[str], priority: list[str]) -> list[FileChange]:
    kept = [f for f in files if not _matches(f.path, exclude) and not _unreviewable(f)]
    sized = sorted(((len(unified_diff(f)), f) for f in kept), key=lambda p: p[0])
    first = [f for _, f in sized if _matches(f.path, priority)]
    rest = [f for _, f in sized if not _matches(f.path, priority)]
    return first + rest
