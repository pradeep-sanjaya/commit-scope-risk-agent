import json

import structlog

from scope_agent.logging import configure_logging


def test_logging_emits_json(capsys):
    configure_logging("INFO")
    structlog.get_logger().info("hello", repo="PAY/x")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "hello" and line["repo"] == "PAY/x" and line["level"] == "info"
    assert "timestamp" in line
