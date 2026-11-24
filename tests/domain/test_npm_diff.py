from pathlib import Path

from scope_agent.domain.npm_diff import npm_changes

FIX = Path(__file__).parent.parent / "fixtures"


def _read(n):
    return (FIX / n).read_text()


def tuples(cs):
    return {(c.name, c.kind, c.old_version, c.new_version) for c in cs}


def test_npm_changes():
    pkg = npm_changes("web/package.json", _read("package_before.json"), _read("package_after.json"))
    assert ("@openfeature/flagd-web-provider", "added", None, "0.6.0") in tuples(pkg)
    lock = npm_changes("web/package-lock.json", _read("lock_before.json"), _read("lock_after.json"))
    assert ("rxjs", "transitive") in {(c.name, c.kind) for c in lock}
    assert not any(c.name == "tslib" for c in lock)
    assert {c.kind for c in lock} == {"transitive"}


def test_package_json_kinds():
    b = '{"dependencies":{"a":"^1.0.0","b":"2.0.0","c":"1.0.0"}}'
    a = '{"dependencies":{"a":"^2.0.0","c":"~0.9.0"},"devDependencies":{"d":">=1.0.0"}}'
    got = {c.name: (c.kind, c.old_version, c.new_version) for c in npm_changes("package.json", b, a)}
    assert got == {
        "a": ("upgraded", "1.0.0", "2.0.0"),
        "b": ("removed", "2.0.0", None),
        "c": ("downgraded", "1.0.0", "0.9.0"),
        "d": ("added", None, "1.0.0"),
    }


def test_npm_malformed_returns_empty():
    assert npm_changes("package.json", "{", "{}") == []
    assert npm_changes("package-lock.json", "{}", "nope") == []
