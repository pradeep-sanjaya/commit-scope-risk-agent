from scope_agent.domain.file_selection import select_files, unified_diff
from scope_agent.domain.models import FileChange


def _fc(path, before="a\n", after="b\n"):
    return FileChange(path=path, status="modified", before=before, after=after)


def test_select_excludes_lock_and_prioritises_application_yml():
    files = [
        _fc("web/package-lock.json"),
        _fc("src/Big.java", "x\n", "y\n" * 50),
        _fc("src/Small.java"),
        _fc("src/main/resources/application.yml"),
        _fc("dist/app.js"),
        _fc("img.png", "a\x00b", "c\x00d"),
    ]
    out = select_files(files, ["**/package-lock.json", "**/dist/**"], ["**/application*.yml"])
    assert [f.path for f in out] == ["src/main/resources/application.yml", "src/Small.java", "src/Big.java"]


def test_root_level_paths_match_double_star_globs():
    out = select_files([_fc("application.yml"), _fc("package-lock.json")], ["**/package-lock.json"], ["**/application*.yml"])
    assert [f.path for f in out] == ["application.yml"]


def test_unchanged_files_dropped_and_diff_has_context():
    same = _fc("a.txt", "x\n", "x\n")
    assert select_files([same], [], []) == []
    d = unified_diff(_fc("a.txt", "1\n2\n3\n4\n5\n6\n7\n8\n", "1\n2\n3\n4\nX\n6\n7\n8\n"))
    assert "--- a/a.txt" in d and "+X" in d and "-5" in d and "\n 2\n" in d and "\n 1\n" not in d
