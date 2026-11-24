import pytest

from scope_agent.settings import load_settings

REQUIRED = {"SCOPE_BITBUCKET_URL": "https://bb.corp", "SCOPE_BITBUCKET_TOKEN": "t", "SCOPE_WEBHOOK_SECRET": "s",
            "SCOPE_JIRA_URL": "https://corp.atlassian.net", "SCOPE_JIRA_EMAIL": "svc@corp", "SCOPE_JIRA_TOKEN": "j",
            "SCOPE_JIRA_PROJECT_KEYS": "PAY,PLAT", "SCOPE_LLM_API_KEY": "k", "SCOPE_NEXUS_URL": "https://nexus.corp",
            "SCOPE_NEXUS_USER": "u", "SCOPE_NEXUS_PASSWORD": "p", "SCOPE_SMTP_HOST": "smtp", "SCOPE_SMTP_FROM": "agent@corp",
            "SCOPE_PUBLIC_BASE_URL": "https://agent.corp", "SCOPE_RATING_SECRET": "r",
            "SCOPE_DATABASE_URL": "sqlite://", "SCOPE_DIGEST_RECIPIENTS": "pmo@corp"}


def set_env(monkeypatch, skip=()):
    for k, v in REQUIRED.items():
        if k not in skip:
            monkeypatch.setenv(k, v)


def test_settings_from_env_with_defaults(monkeypatch, data_files):
    set_env(monkeypatch)
    s = load_settings()
    assert s.jira_project_keys == ["PAY", "PLAT"]
    assert s.branches == ["development", "hotfix/*", "release/*"]
    assert s.retry_backoff_seconds == [30, 120, 600]
    assert s.bitbucket_token.get_secret_value() == "t"
    assert s.port == 8080
    assert s.llm_model == "claude-sonnet-5-5"
    assert s.report_debounce_seconds == 300
    assert s.portal_auth_header is None
    assert s.areas_file.exists()


def test_list_env_override(monkeypatch, data_files):
    set_env(monkeypatch)
    monkeypatch.setenv("SCOPE_BRANCHES", "main, release/*")
    assert load_settings().branches == ["main", "release/*"]


def test_missing_required_exits_naming_variable(monkeypatch, data_files, capsys):
    set_env(monkeypatch, skip={"SCOPE_JIRA_TOKEN"})
    with pytest.raises(SystemExit) as e:
        load_settings()
    assert e.value.code == 2 and "SCOPE_JIRA_TOKEN" in capsys.readouterr().err


def test_missing_routing_file_exits(monkeypatch, data_files, capsys):
    set_env(monkeypatch)
    monkeypatch.setenv("SCOPE_ROUTING_FILE", "/nonexistent.yaml")
    with pytest.raises(SystemExit) as e:
        load_settings()
    assert e.value.code == 2
    assert "SCOPE_ROUTING_FILE" in capsys.readouterr().err


def test_port_unprefixed(monkeypatch, data_files):
    set_env(monkeypatch)
    monkeypatch.setenv("PORT", "9000")
    assert load_settings().port == 9000
