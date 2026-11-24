from scope_agent.domain.dependency_files import dependency_changes
from scope_agent.domain.models import FileChange


def _fc(path, before, after):
    return FileChange(path=path, status="modified", before=before, after=after)


def test_dispatch():
    pom = '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency><groupId>g</groupId><artifactId>a</artifactId><version>1</version></dependency></dependencies></project>'
    ch = dependency_changes(_fc("a/pom.xml", None, pom))
    assert [c.ecosystem for c in ch] == ["maven"] and ch[0].file == "a/pom.xml"
    ch = dependency_changes(_fc("w/package.json", "{}", '{"dependencies":{"x":"1.0.0"}}'))
    assert [c.ecosystem for c in ch] == ["npm"]
    assert dependency_changes(_fc("README.md", "a", "b")) == []


def test_dispatch_basename_only():
    assert dependency_changes(_fc("a/my-pom.xml", "x", "y")) == []
