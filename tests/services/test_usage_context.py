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
