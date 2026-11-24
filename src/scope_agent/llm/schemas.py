from typing import Literal

from pydantic import BaseModel, Field

from scope_agent.domain.models import FindingType, Severity

RISK_CATEGORIES: tuple[str, ...] = (
    # runtime behavior
    "connection_model", "connection_pool", "retries_timeouts", "caching", "resource_usage",
    "tls_security", "serialization", "config_removed", "concurrency",
    # change type
    "schema_migration", "auth_config", "external_call", "feature_flag_defaults", "infra_config",
    # dependency-specific
    "new_dependency", "platform_upgrade", "behavior_unknown", "transitive_major",
)


class LLMFinding(BaseModel):
    type: FindingType
    category: str
    severity: Severity
    title: str
    explanation: str
    evidence: list[str]
    files: list[str] = Field(default_factory=list)


class DependencyVerdict(BaseModel):
    findings: list[LLMFinding]
    reviewer_summary: str


class CodeVerdict(BaseModel):
    findings: list[LLMFinding]
    ac_quality: Literal["ok", "vague", "missing"]
    ac_quality_reason: str
    test_note: list[str] = Field(description="3-6 bullets on what this commit needs tested and why")


class TestScopeItem(BaseModel):
    __test__ = False
    type: Literal["functional", "integration", "regression", "non_functional", "data", "environment"]
    what: str
    why: str
    tickets: list[str]


class TestScopeVerdict(BaseModel):
    __test__ = False
    items: list[TestScopeItem]
