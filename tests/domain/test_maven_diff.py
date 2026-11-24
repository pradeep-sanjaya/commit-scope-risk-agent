from pathlib import Path

from scope_agent.domain.maven_diff import maven_changes

FIX = Path(__file__).parent.parent / "fixtures"


def _read(n):
    return (FIX / n).read_text()


def test_maven_changes():
    got = {(c.name, c.kind, c.old_version, c.new_version) for c in maven_changes("svc/pom.xml", _read("pom_before.xml"), _read("pom_after.xml"))}
    assert got == {
        ("com.corp:flag-util", "removed", "1.3.0", None),
        ("dev.openfeature.contrib.providers:flagd", "added", None, "0.8.1"),
        ("com.squareup.okhttp3:okhttp", "upgraded", "4.9.0", "4.12.0"),
        ("org.springframework.boot:spring-boot-starter-parent", "parent", "3.2.0", "3.3.0"),
        ("com.corp:platform-bom", "bom", "1.0.0", "2.0.0"),
    }


def _pom(dep_version):
    return (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>'
        f"<groupId>g</groupId><artifactId>a</artifactId><version>{dep_version}</version>"
        "</dependency></dependencies></project>"
    )


def test_maven_unresolved_property_kept_raw():
    ch = maven_changes("pom.xml", _pom("${a.version}"), _pom("${b.version}"))
    assert [(c.kind, c.old_version, c.new_version) for c in ch] == [("upgraded", "${a.version}", "${b.version}")]
    assert maven_changes("pom.xml", _pom("${a.version}"), _pom("${a.version}")) == []


def test_maven_new_file_all_added():
    ch = maven_changes("pom.xml", None, _read("pom_after.xml"))
    assert {c.kind for c in ch} == {"added", "parent", "bom"}
    assert any(c.name == "com.squareup.okhttp3:okhttp" and c.new_version == "4.12.0" for c in ch)


def test_maven_downgrade_and_equal():
    assert maven_changes("pom.xml", _pom("2.0"), _pom("1.0"))[0].kind == "downgraded"
    assert maven_changes("pom.xml", _pom("1.0"), _pom("1.0.0")) == []


def test_maven_malformed_returns_empty():
    assert maven_changes("pom.xml", "<project", _pom("1.0")) == []
    assert maven_changes("pom.xml", _pom("1.0"), "not xml") == []
