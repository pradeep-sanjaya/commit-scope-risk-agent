from scope_agent.domain.versions import is_major_bump, version_key, versions_between


def test_versions():
    assert version_key("1.10.0") > version_key("1.9.3") and version_key("2.0.0-rc1") < version_key("2.0.0")
    assert versions_between(["0.7.0", "0.8.0", "0.8.1", "1.0.0"], "0.7.0", "0.8.1") == ["0.8.0", "0.8.1"]
    assert is_major_bump("1.4.2", "2.0.0") and is_major_bump("0.7.0", "0.8.0") and not is_major_bump("1.4.2", "1.5.0")


def test_odd_inputs_do_not_raise_and_sort_lowest():
    assert version_key("${unresolved}") < version_key("0.0.1")
    assert version_key("1.0.0.Final") is not None
    sorted(["1.0", "abc", "", "1.0.0.Final", "2.0-SNAPSHOT", "1.0+build5"], key=version_key)


def test_prerelease_variants_sort_before_release():
    for q in ["SNAPSHOT", "alpha", "beta", "M1", "rc1"]:
        assert version_key(f"1.0.0-{q}") < version_key("1.0.0")


def test_versions_between_skips_unparseable():
    assert versions_between(["1.0", "junk", "1.5", "2.0"], "1.0", "2.0") == ["1.5", "2.0"]


def test_is_major_bump_unparseable_false():
    assert not is_major_bump("${x}", "2.0.0")
