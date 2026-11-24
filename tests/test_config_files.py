from pathlib import Path

import pytest

from scope_agent.config_files import (
    ConfigFileError,
    load_areas,
    load_dependency_hints,
    load_routing,
)

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def test_load_routing_example():
    cfg = load_routing(DEPLOY / "routing.example.yaml")
    assert cfg.repos["payments-service"].leads == ["lead-a@corp"]
    assert cfg.repos["payments-service"].paths["src/main/java/**/flags/**"] == ["flags-owner@corp"]
    assert cfg.categories["timeouts"] == ["platform-sre@corp"]
    assert cfg.default_leads == ["eng-managers@corp"]


def test_routing_requires_default_leads(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("repos: {}\n")
    with pytest.raises(ConfigFileError):
        load_routing(p)
    p.write_text("default_leads: []\n")
    with pytest.raises(ConfigFileError):
        load_routing(p)


def test_load_hints_example():
    h = load_dependency_hints(DEPLOY / "dependency-hints.example.yaml")
    assert h.internal_repos["com.corp:flag-util"] == "PLAT/flag-util"
    assert "dev.openfeature.contrib.providers:flagd" in h.library_docs
    assert h.known_swaps == []


def test_load_areas_example_and_area_for():
    m = load_areas(DEPLOY / "areas.example.yaml")
    path = "src/main/java/x/checkout/Api.java"
    assert m.area_for("payments-service", path) == "Checkout API"
    assert m.area_for("PAY/payments-service", path) == "Checkout API"
    assert m.area_for("PAY/payments-service", "web/src/app/x.ts") == "payments-service: web"
    assert m.area_for("PAY/payments-service", "README.md") == "payments-service: (root)"
    assert m.area_for("PAY/payments-service", "src/a/flags/F.java") == "Feature flags"


def test_star_rule_matches_any_repo():
    m = load_areas(DEPLOY / "areas.example.yaml")
    assert m.area_for("X/anything", "docs/guide.md") == "Documentation"


def test_bad_yaml_raises_config_file_error_with_path(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("a: [unclosed\n")
    for loader in (load_routing, load_dependency_hints, load_areas):
        with pytest.raises(ConfigFileError) as ei:
            loader(p)
        assert ei.value.path == p
        assert str(p) in str(ei.value)


def test_schema_error_raises(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("areas: [1, 2]\n")
    with pytest.raises(ConfigFileError):
        load_areas(p)
