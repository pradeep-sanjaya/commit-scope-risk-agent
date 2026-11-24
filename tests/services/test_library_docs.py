from unittest.mock import MagicMock

from scope_agent.domain.dependency_hints import DependencyHints
from scope_agent.domain.models import DependencyChange
from scope_agent.services.library_docs import LibraryDocs, LibraryDocsService
from scope_agent.services.source_resolver import SourceRepo
from tests.services.test_release_notes import FakeCache


def _change(name="io.x:flagd", old=None, new="1.2.0"):
    return DependencyChange(ecosystem="maven", name=name, old_version=old, new_version=new, kind="added", file="pom.xml")


def _svc(source, github=None, bitbucket=None, pages=None, hints=None, max_chars=1000, cache=None):
    resolver = MagicMock()
    resolver.resolve.return_value = source
    return LibraryDocsService(
        resolver, github or MagicMock(), bitbucket or MagicMock(), pages or MagicMock(),
        hints or DependencyHints(), max_chars, cache or FakeCache(),
    )


def test_docs_readme_plus_hint_page_html_stripped():
    github = MagicMock()
    github.readme.return_value = "# flagd\nEvaluates flags"
    pages = MagicMock()
    pages.text.return_value = "Guide text"
    hints = DependencyHints(library_docs={"io.x:flagd": ["https://docs.test/flagd"]})
    docs = _svc(SourceRepo("github", "o/r", "sub"), github, pages=pages, hints=hints).docs_for(_change())
    github.readme.assert_called_once_with("o/r", "sub")
    pages.text.assert_called_once_with("https://docs.test/flagd")
    assert "Evaluates flags" in docs.text and "Guide text" in docs.text
    assert docs.library == "io.x:flagd" and docs.version == "1.2.0"
    assert docs.links == ["https://github.com/o/r", "https://docs.test/flagd"]


def test_docs_truncated_to_max_chars():
    github = MagicMock()
    github.readme.return_value = "x" * 5000
    docs = _svc(SourceRepo("github", "o/r", None), github, max_chars=100).docs_for(_change())
    assert len(docs.text) == 100


def test_docs_bitbucket_readme_at_tag_and_cache():
    bb = MagicMock()
    bb.list_tags.return_value = ["v1.2.0"]
    bb.get_file.return_value = "internal readme"
    cache = FakeCache()
    svc = _svc(SourceRepo("bitbucket", "P/r", None), bitbucket=bb, cache=cache)
    docs = svc.docs_for(_change())
    bb.get_file.assert_called_once_with("P/r", "README.md", "v1.2.0")
    assert docs.text == "internal readme"
    assert cache.puts == [("library_docs", "io.x:flagd@1.2.0", None)]
    assert svc.docs_for(_change()) == docs
    assert bb.get_file.call_count == 1


def test_docs_uses_old_version_and_empty_not_cached():
    cache = FakeCache()
    github = MagicMock()
    github.readme.return_value = None
    docs = _svc(SourceRepo("github", "o/r", None), github, cache=cache).docs_for(_change(old="1.0", new=None))
    assert docs == LibraryDocs(library="io.x:flagd", version="1.0", text="", links=[])
    assert cache.puts == []
