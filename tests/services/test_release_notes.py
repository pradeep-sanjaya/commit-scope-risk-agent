import json
from unittest.mock import MagicMock

import pytest

from scope_agent.domain.models import DependencyChange
from scope_agent.services.release_notes import ReleaseNotes, ReleaseNotesService
from scope_agent.services.source_resolver import SourceRepo


class FakeCache:
    def __init__(self):
        self.data = {}
        self.puts = []

    def get(self, ns, key):
        return self.data.get((ns, key))

    def put(self, ns, key, value, ttl_seconds):
        self.puts.append((ns, key, ttl_seconds))
        self.data[(ns, key)] = value


def _change(name="dev.openfeature.contrib:flagd", old="0.7.0", new="0.8.1"):
    return DependencyChange(ecosystem="maven", name=name, old_version=old, new_version=new, kind="upgraded", file="pom.xml")


def _svc(source, github=None, bitbucket=None, cache=None):
    resolver = MagicMock()
    resolver.resolve.return_value = source
    return ReleaseNotesService(resolver, github or MagicMock(), bitbucket or MagicMock(), cache or FakeCache())


GH = SourceRepo("github", "o/r", None)


def _rel(tag, body="notes"):
    return {"tag_name": tag, "body": body, "html_url": f"https://github.com/o/r/releases/tag/{tag}"}


def test_between_monorepo_tags():
    github = MagicMock()
    github.releases.return_value = [
        _rel("flagd-v0.8.1", "b81"), _rel("flagd-v0.8.0", "b80"), _rel("ofrep-v0.1.0"), _rel("flagd-v0.7.0"),
    ]
    notes = _svc(GH, github).between(_change())
    assert [v for v, _ in notes.entries] == ["0.8.0", "0.8.1"]
    assert notes.entries[1] == ("0.8.1", "b81")
    assert notes.available and notes.library == "dev.openfeature.contrib:flagd"
    assert notes.links == [
        "https://github.com/o/r/releases/tag/flagd-v0.8.0", "https://github.com/o/r/releases/tag/flagd-v0.8.1"]


def test_between_changelog_fallback():
    github = MagicMock()
    github.releases.return_value = []
    changelog = "# Changelog\n\n## [0.8.1] - 2024\n- default is now streaming\n\n## 0.8.0\n- x\n\n## 0.7.0\n- old\n"
    github.file.side_effect = lambda repo, path: changelog if path == "CHANGELOG.md" else None
    notes = _svc(SourceRepo("github", "o/r", "pkg"), github).between(_change())
    assert [v for v, _ in notes.entries] == ["0.8.0", "0.8.1"]
    assert "streaming" in dict(notes.entries)["0.8.1"]
    assert "old" not in dict(notes.entries)["0.8.0"]
    assert notes.links == ["https://github.com/o/r/blob/HEAD/CHANGELOG.md"]
    assert github.file.call_args_list[0].args == ("o/r", "pkg/CHANGELOG.md")  # subpath first


def test_between_rate_limited_unavailable_cached_short():
    github = MagicMock()
    github.releases.return_value = None
    cache = FakeCache()
    notes = _svc(GH, github, cache=cache).between(_change())
    assert notes == ReleaseNotes(library="dev.openfeature.contrib:flagd", entries=[], links=[], available=False)
    assert cache.puts == [("release_notes", "dev.openfeature.contrib:flagd@0.7.0..0.8.1", 6 * 3600)]
    github.file.assert_not_called()


def test_between_bitbucket_commit_messages():
    bb = MagicMock()
    bb.list_tags.return_value = ["flagd-0.7.0", "v0.8.1", "other"]
    bb.commit_messages_between.return_value = ["feat: streaming default", "fix: x"]
    notes = _svc(SourceRepo("bitbucket", "PLAT/flagd", None), bitbucket=bb).between(_change())
    bb.commit_messages_between.assert_called_once_with("PLAT/flagd", "flagd-0.7.0", "v0.8.1")
    assert notes.available
    assert notes.entries == [("0.7.0..0.8.1", "feat: streaming default\nfix: x")]
    assert len(notes.links) == 1 and "flagd-0.7.0" in notes.links[0]


def test_bitbucket_missing_tag_unavailable():
    bb = MagicMock()
    bb.list_tags.return_value = ["v0.8.1"]
    assert not _svc(SourceRepo("bitbucket", "P/r", None), bitbucket=bb).between(_change()).available


def test_unknown_source_and_missing_versions():
    assert not _svc(SourceRepo("unknown", "", None)).between(_change()).available
    svc = _svc(GH)
    for c in (_change(old=None), _change(new=None)):
        notes = svc.between(c)
        assert notes == ReleaseNotes(library=c.name, entries=[], links=[], available=False)


def test_between_cached_permanent_when_available():
    github = MagicMock()
    github.releases.return_value = [_rel("flagd-v0.8.1")]
    cache = FakeCache()
    svc = _svc(GH, github, cache=cache)
    first = svc.between(_change())
    assert cache.puts[0][2] is None
    assert json.loads(cache.data[("release_notes", "dev.openfeature.contrib:flagd@0.7.0..0.8.1")])["available"] is True
    second = svc.between(_change())
    assert second == first
    assert github.releases.call_count == 1
