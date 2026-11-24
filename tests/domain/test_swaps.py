from datetime import datetime, timezone

from scope_agent.domain.models import CommitData, DependencyChange, FileChange
from scope_agent.domain.swaps import Swap, detect_swaps, import_prefixes


def dep(eco, name, kind, file="pom.xml"):
    return DependencyChange(
        ecosystem=eco, name=name, old_version=None, new_version=None, kind=kind, file=file
    )


def commit(path, before, after, status="modified"):
    return CommitData(
        repo="P/r", sha="a", author_name="n", author_email=None, message="m", parents=[],
        files=[FileChange(path=path, status=status, before=before, after=after)],
        committed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


OLD = dep("maven", "com.corp:flag-util", "removed")
NEW = dep("maven", "dev.openfeature.contrib.providers:flagd", "added")


def test_no_swap_without_evidence():
    assert detect_swaps([OLD, NEW], [], []) == []


def test_swap_by_import_change():
    c = commit(
        "Flags.java",
        "import com.corp.flagutil.FlagClient;\nclass A {}\n",
        "import dev.openfeature.contrib.providers.flagd.FlagdProvider;\nclass A {}\n",
    )
    assert detect_swaps([OLD, NEW], [c], []) == [Swap(removed=OLD, added=NEW, reason="import_change")]


def test_swap_by_ts_import_change():
    o, n = dep("npm", "old-lib", "removed"), dep("npm", "@x/new-sdk", "added")
    c = commit("a.ts", "import { A } from 'old-lib/sub';\n", 'import { B } from "@x/new-sdk";\n')
    assert detect_swaps([o, n], [c], [])[0].reason == "import_change"


def test_swap_configured():
    s = detect_swaps([OLD, NEW], [], [("com.corp:flag-util", "dev.openfeature.contrib.providers:flagd")])
    assert [x.reason for x in s] == ["configured"]


def test_swap_name_overlap_npm():
    o, n = dep("npm", "ngx-feature-toggle", "removed"), dep("npm", "@openfeature/feature-toggle-web", "added")
    assert detect_swaps([o, n], [], []) == [Swap(removed=o, added=n, reason="name_overlap")]


def test_no_swap_across_ecosystems():
    n = dep("npm", "flag-util", "added")
    assert detect_swaps([OLD, n], [], [("com.corp:flag-util", "flag-util")]) == []


def test_only_added_removed_and_one_swap_each():
    up = dep("maven", "com.corp:flag-util2", "upgraded")
    n2 = dep("maven", "x:flag-other", "added")
    s = detect_swaps([OLD, up], [], [])
    assert s == []
    s = detect_swaps([OLD, NEW, n2], [], [("com.corp:flag-util", "x:flag-other")])
    assert len(s) == 1 and s[0].added == n2 and s[0].reason == "configured"


def test_import_prefixes():
    assert import_prefixes(OLD) == ["com.corp", "com.corp.flagutil"]
    assert import_prefixes(dep("npm", "@a/b", "added")) == ["@a/b"]
