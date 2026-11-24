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
