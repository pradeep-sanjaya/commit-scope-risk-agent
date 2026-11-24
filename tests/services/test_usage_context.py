from datetime import datetime, timezone
from unittest.mock import MagicMock

from scope_agent.domain.models import CommitData, DependencyChange, FileChange
from scope_agent.services.usage_context import UsageContextBuilder


def _commit(files):
    return CommitData(repo="P/r", sha="abc", author_name="A", author_email="a@x.test", message="m",
                      parents=["p"], files=files, committed_at=datetime.now(timezone.utc))


CHANGE = DependencyChange(ecosystem="maven", name="io.x:flagd", old_version=None, new_version="1", kind="added", file="pom.xml")


def test_usage_context_includes_diff_and_app_config_capped():
    bb = MagicMock()
    bb.list_files.return_value = ["src/application.yml"]
    bb.get_file.return_value = "flagd:\n  host: " + "h" * 5000
    commit = _commit([
        FileChange(path="A.java", status="modified", before="class A {}\n", after="import io.x.flagd.Client;\nclass A {}\n"),
        FileChange(path="B.java", status="modified", before="x\n", after="y\n"),
    ])
    out = UsageContextBuilder(bb, max_chars=300).build(commit, CHANGE)
    assert "A.java" in out and "+import io.x.flagd.Client;" in out
    assert "B.java" not in out
    assert "application.yml" in out
    assert len(out) <= 300
    globs = bb.list_files.call_args.args[2]
    assert "**/application*.y*ml" in globs and "**/environment*.ts" in globs
    assert bb.list_files.call_args.args[:2] == ("P/r", "abc")


def test_usage_context_empty_when_nothing_found():
    bb = MagicMock()
    bb.list_files.return_value = []
    assert UsageContextBuilder(bb, max_chars=100).build(_commit([]), CHANGE) == ""


def test_only_files_with_changed_import_of_library_included():
    corp = DependencyChange(ecosystem="maven", name="com.corp:flag-util", old_version="1", new_version=None, kind="removed", file="pom.xml")
    files = [
        FileChange(path=f"P{i}.java", status="modified", before=f"package com.corp.app;\nint a{i};\n", after=f"package com.corp.app;\nint b{i};\n")
        for i in range(3)
    ]
    files.append(FileChange(path="Flags.java", status="modified",
                            before="package com.corp.app;\nimport com.corp.flagutil.Flags;\n",
                            after="package com.corp.app;\nimport dev.x.Flags;\n"))
    bb = MagicMock()
    bb.list_files.return_value = []
    out = UsageContextBuilder(bb, 5000).build(_commit(files), corp)
    assert "Flags.java" in out and "P0.java" not in out and "P1.java" not in out


def test_config_survives_truncation_of_large_diff():
    bb = MagicMock()
    bb.list_files.return_value = ["application.yml"]
    bb.get_file.return_value = "flagd:\n  pollInterval: 5s\n"
    big = "\n".join(f"import io.x.flagd.C{i};" for i in range(500))
    commit = _commit([FileChange(path="A.java", status="modified", before="", after=big)])
    out = UsageContextBuilder(bb, 1000).build(commit, CHANGE)
    assert "pollInterval: 5s" in out and "A.java" in out and len(out) <= 1000
