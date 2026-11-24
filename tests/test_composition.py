import ast
import re
from pathlib import Path

from scope_agent.composition import Container, build_container
from scope_agent.settings import load_settings
from tests.test_settings import set_env

SRC = Path(__file__).parent.parent / "src" / "scope_agent"
ADAPTERS = {"BitbucketClient", "JiraClient", "NexusClient", "GitHubClient", "SmtpSender", "AnthropicClient"}
ALLOWED = {"composition.py", "llm/factory.py"}


def test_build_container_from_env(monkeypatch, data_files):
    data_files["SCOPE_ROUTING_FILE"].write_text("default_leads: [lead@corp]\n")
    set_env(monkeypatch)
    c = build_container(load_settings())
    assert isinstance(c, Container)
    assert all(getattr(c, name) is not None for name in Container.__dataclass_fields__)


def test_no_module_reads_os_environ():
    offenders = [
        str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
        if p.name != "settings.py" and re.search(r"os\.environ|os\.getenv", p.read_text())
    ]
    assert offenders == []


def test_only_composition_constructs_adapters():
    offenders = []
    for p in SRC.rglob("*.py"):
        rel = p.relative_to(SRC).as_posix()
        if rel in ALLOWED:
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                if name in ADAPTERS:
                    offenders.append(f"{rel}:{node.lineno} {name}")
    assert offenders == []


def test_startup_log_masks_secrets(monkeypatch, data_files, capsys):
    from scope_agent.logging import configure_logging
    data_files["SCOPE_ROUTING_FILE"].write_text("default_leads: [lead@corp]\n")
    set_env(monkeypatch)
    monkeypatch.setenv("SCOPE_DATABASE_URL", "sqlite://")
    monkeypatch.setenv("SCOPE_BITBUCKET_TOKEN", "bb-t0ken-xyz")
    settings = load_settings()
    settings = settings.model_copy(update={"database_url": "postgresql+psycopg://scope:s3cr3t@db:5432/scope"})
    configure_logging("INFO")
    from scope_agent import composition
    monkeypatch.setattr(composition, "Database", lambda url: __import__("scope_agent.persistence.database", fromlist=["Database"]).Database("sqlite://"))
    build_container(settings)
    out = capsys.readouterr().out
    assert "startup_config" in out and "scope:***@db:5432" in out
    assert "s3cr3t" not in out
    assert "bb-t0ken-xyz" not in out
