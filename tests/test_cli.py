import json
from types import SimpleNamespace

from typer.testing import CliRunner

from scope_agent import cli
from scope_agent.domain.models import Finding, FindingType, LibrarySummary, Severity
from scope_agent.services.pipeline import PushResult
from tests.test_settings import set_env

runner = CliRunner()


def _finding():
    return Finding(repo="P/r", commit="abc1234567", author_email=None, jira_keys=[],
                   type=FindingType.risky_change, category="connection_model", severity=Severity.high,
                   title="Switched flag provider", explanation="why", evidence=["e"], files=["pom.xml"],
                   suggested_reviewers=[])


def _fake_container(**over):
    seen = {}

    class Pipeline:
        def run(self, job_id, job, force=False):
            seen["job"], seen["force"] = job, force
            return PushResult(
                job_id=job_id, job=job, findings=[_finding()],
                summaries={"abc1234567": [LibrarySummary(library="flagd", kind="swap", summary="s", links=[])]},
            )

    class Bitbucket:
        def get_commit(self, repo, sha):
            return SimpleNamespace(parents=["parent1"])

    base = dict(pipeline=Pipeline(), source=Bitbucket(), notifier=SimpleNamespace(notify=lambda r: seen.setdefault("sent", True)))
    base.update(over)
    return SimpleNamespace(**base), seen


def _patch(monkeypatch, data_files, container):
    set_env(monkeypatch)
    monkeypatch.setattr(cli, "build_container", lambda settings: container)


def test_cli_analyze_json(monkeypatch, data_files):
    container, seen = _fake_container()
    _patch(monkeypatch, data_files, container)
    res = runner.invoke(cli.app, ["analyze", "PAY/svc", "abc1234567", "--json"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["findings"][0]["title"] == "Switched flag provider"
    assert out["summaries"]["abc1234567"][0]["library"] == "flagd"
    assert seen["force"] is True
    assert (seen["job"].from_hash, seen["job"].to_hash) == ("parent1", "abc1234567")
    assert "sent" not in seen


def test_cli_analyze_send_email(monkeypatch, data_files):
    container, seen = _fake_container()
    _patch(monkeypatch, data_files, container)
    res = runner.invoke(cli.app, ["analyze", "PAY/svc", "abc1234567", "--send-email"])
    assert res.exit_code == 0 and seen["sent"] is True
    assert "Switched flag provider" in res.stdout


def test_cli_report_useful_rate(monkeypatch, data_files):
    rows = [
        {"type": "risky_change", "severity": "high", "useful": 8, "noise": 2},
        {"type": "ticket_gap", "severity": "medium", "useful": 3, "noise": 3},
        {"type": "ticket_gap", "severity": "low", "useful": 0, "noise": 5},
    ]
    container = SimpleNamespace(ratings=SimpleNamespace(report=lambda: rows))
    _patch(monkeypatch, data_files, container)
    res = runner.invoke(cli.app, ["report"])
    assert res.exit_code == 0, res.output
    assert "80%" in res.stdout  # high row
    assert "69%" in res.stdout and "below target 70%" in res.stdout  # (8+3)/(10+6)


def test_cli_exits_2_on_missing_env(monkeypatch, data_files):
    monkeypatch.delenv("SCOPE_JIRA_TOKEN", raising=False)
    set_env(monkeypatch, skip={"SCOPE_JIRA_TOKEN"})
    res = runner.invoke(cli.app, ["report"])
    assert res.exit_code == 2


def test_cli_web_serves_portal(monkeypatch, data_files):
    portal = object()
    container = SimpleNamespace(jobs=object(), ratings=object(), portal=portal)
    _patch(monkeypatch, data_files, container)
    monkeypatch.setenv("SCOPE_PORTAL_AUTH_HEADER", "X-Forwarded-User")
    seen = {}
    monkeypatch.setattr(cli, "create_app", lambda **kw: seen.update(kw) or "app")
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.setdefault("ran", app))
    res = runner.invoke(cli.app, ["web"])
    assert res.exit_code == 0, res.output
    assert seen["portal_queries"] is portal and seen["portal_auth_header"] == "X-Forwarded-User"
    assert seen["source_provider"] == "bitbucket"
    assert seen["ran"] == "app"
