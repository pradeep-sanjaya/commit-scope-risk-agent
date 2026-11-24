"""Wiring check for the motivating incident: a client library swapped for flagd."""

from datetime import datetime, timezone

from scope_agent import composition
from scope_agent.domain.models import CommitData, FileChange, FindingType, Severity
from scope_agent.llm.base import LLMResult
from scope_agent.llm.schemas import CodeVerdict, DependencyVerdict, LLMFinding
from scope_agent.settings import load_settings
from tests.test_settings import set_env

POM = """<project xmlns="http://maven.apache.org/POM/4.0.0"><dependencies><dependency>
<groupId>{g}</groupId><artifactId>{a}</artifactId><version>{v}</version></dependency></dependencies></project>"""
JAVA = "package x;\nimport {imp};\nclass Flags {{}}\n"
TITLE = "Flag provider now streams over a long-lived connection"


class FakeBitbucket:
    def __init__(self, http):
        pass

    def commits_in_push(self, job, limit):
        return ["swap1"]

    def get_commit(self, repo, sha):
        return CommitData(
            repo=repo, sha=sha, author_name="Dev", author_email="dev@corp", message="Use flagd",
            parents=["p0"], committed_at=datetime.now(timezone.utc),
            files=[
                FileChange(path="pom.xml", status="modified",
                           before=POM.format(g="com.corp", a="flag-util", v="1.3.0"),
                           after=POM.format(g="dev.openfeature.contrib.providers", a="flagd", v="0.8.1")),
                FileChange(path="src/main/java/Flags.java", status="modified",
                           before=JAVA.format(imp="com.corp.flagutil.FlagClient"),
                           after=JAVA.format(imp="dev.openfeature.contrib.providers.flagd.FlagdProvider")),
            ],
        )

    def get_file(self, *a, **k):
        return None

    def list_files(self, *a, **k):
        return []

    def list_tags(self, *a, **k):
        return []

    def commit_messages_between(self, *a, **k):
        return []


class Nothing:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeLLM:
    def analyze(self, system, prompt, schema):
        if schema is DependencyVerdict:
            finding = LLMFinding(type=FindingType.dependency_behavior_change, category="connection_model",
                                 severity=Severity.high, title=TITLE, explanation="polling became streaming",
                                 evidence=["flagd docs"], files=[])
            data = DependencyVerdict(findings=[finding], reviewer_summary="OpenFeature flagd provider")
        else:
            data = CodeVerdict(findings=[], ac_quality="ok", ac_quality_reason="", test_note=[])
        return LLMResult(data=data, input_tokens=1, output_tokens=1)


def test_incident_replay_wiring(monkeypatch, data_files):
    data_files["SCOPE_ROUTING_FILE"].write_text(
        "categories:\n  connection_model: [gateway-owner@corp]\ndefault_leads: [eng-managers@corp]\n"
    )
    set_env(monkeypatch)
    sent = []

    class FakeSender:
        def __init__(self, *a, **k):
            pass

        def send(self, msg, to):
            sent.append((msg, to))

    monkeypatch.setattr(composition, "BitbucketClient", FakeBitbucket)
    for name in ("JiraClient", "NexusClient", "GitHubClient", "WebPageFetcher"):
        monkeypatch.setattr(composition, name, Nothing)
    monkeypatch.setattr(composition, "SmtpSender", FakeSender)
    monkeypatch.setattr(composition, "build_llm", lambda settings, meter: FakeLLM())

    c = composition.build_container(load_settings())
    c.db.create_all()
    from scope_agent.domain.models import PushJob
    job = PushJob(repo="PAY/svc", branch="development", from_hash="p0", to_hash="swap1", pushed_by=None)
    result = c.pipeline.run(1, job)
    c.notifier.notify(result)

    assert len(sent) == 1
    msg, to = sent[0]
    assert "gateway-owner@corp" in to
    body = msg.get_body(("plain",)).get_content()
    assert TITLE in body
    assert "Library changes" in body and "flagd" in body
