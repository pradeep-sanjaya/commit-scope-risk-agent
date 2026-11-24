from unittest.mock import MagicMock

from scope_agent.domain.models import DependencyChange
from scope_agent.services.library_profile import LibraryProfileBuilder
from scope_agent.services.source_resolver import SourceRepo

CHANGE = DependencyChange(ecosystem="maven", name="com.acme:flags", old_version="1.0", new_version=None, kind="removed", file="pom.xml")


def test_profile_none_for_github_source():
    resolver = MagicMock()
    resolver.resolve.return_value = SourceRepo("github", "o/r", None)
    bb = MagicMock()
    assert LibraryProfileBuilder(resolver, bb, 1000).profile(CHANGE) is None
    bb.get_file.assert_not_called()


def test_profile_bitbucket_readme_and_matching_files_capped_at_8():
    resolver = MagicMock()
    resolver.resolve.return_value = SourceRepo("bitbucket", "LIB/flags", None)
    bb = MagicMock()
    bb.list_tags.return_value = ["v1.0"]
    files = [f"src/Poller{i}.java" for i in range(12)] + ["src/Other.java"]
    bb.list_files.return_value = files

    def get_file(repo, path, at):
        assert at == "v1.0"
        return "# readme" if path == "README.md" else ("code" if "Poller" in path else "nothing")

    bb.get_file.side_effect = get_file
    out = LibraryProfileBuilder(resolver, bb, 10_000).profile(CHANGE)
    assert "# readme" in out
    assert out.count("src/Poller") == 8
    assert "Other.java" not in out


def test_profile_content_match_and_none_without_tag():
    resolver = MagicMock()
    resolver.resolve.return_value = SourceRepo("bitbucket", "LIB/flags", None)
    bb = MagicMock()
    bb.list_tags.return_value = []
    assert LibraryProfileBuilder(resolver, bb, 100).profile(CHANGE) is None
    bb.list_tags.return_value = ["1.0"]
    bb.list_files.return_value = ["src/Thing.java"]
    bb.get_file.side_effect = lambda r, p, a: "schedule every 5s" if p == "src/Thing.java" else None
    out = LibraryProfileBuilder(resolver, bb, 100).profile(CHANGE)
    assert "schedule every 5s" in out
