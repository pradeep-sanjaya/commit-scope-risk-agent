from scope_agent.domain.jira_keys import extract_keys


def test_extract_keys_conventional_commit():
    assert extract_keys("feat(PAY-12): add flag client\n\nRefs: PAY-13", None, ["PAY"]) == ["PAY-12", "PAY-13"]


def test_extract_keys_ignores_unknown_projects():
    assert extract_keys("fix: UTF-8 handling", None, ["PAY"]) == []


def test_extract_keys_branch_fallback():
    assert extract_keys("fix: typo", "feature/PAY-99-typo", ["PAY"]) == ["PAY-99"]


def test_extract_keys_branch_ignored_when_message_has_key():
    assert extract_keys("PAY-1 x", "feature/PAY-99", ["PAY"]) == ["PAY-1"]


def test_extract_keys_unique_ordered():
    assert extract_keys("PAY-2 PAY-1 PAY-2", None, ["PAY"]) == ["PAY-2", "PAY-1"]
