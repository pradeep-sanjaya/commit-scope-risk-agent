import sys
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_core import InitErrorDetails
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]
CsvIntList = Annotated[list[int], NoDecode]

_FILE_FIELDS = ("routing_file", "dependency_hints_file", "areas_file")
_PROVIDER_FIELDS = {"bitbucket": ("bitbucket_url", "bitbucket_token"), "github": ("github_token",)}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCOPE_", extra="ignore", env_ignore_empty=True)

    # Source control: SCOPE_BITBUCKET_* or SCOPE_GITHUB_TOKEN are required for the chosen provider.
    webhook_secret: SecretStr
    # JIRA
    jira_url: str
    jira_email: str
    jira_token: SecretStr
    jira_project_keys: CsvList
    # LLM
    llm_api_key: SecretStr
    # Nexus
    nexus_url: str
    nexus_user: str
    nexus_password: SecretStr
    # SMTP and links
    smtp_host: str
    smtp_from: str
    public_base_url: str
    rating_secret: SecretStr
    # Storage and recipients
    database_url: str
    digest_recipients: CsvList
    # Data files
    routing_file: Path
    dependency_hints_file: Path
    areas_file: Path

    # Source control
    source_provider: Literal["bitbucket", "github"] = "bitbucket"
    bitbucket_url: str | None = None
    bitbucket_token: SecretStr | None = None
    github_url: str = "https://github.com"  # web base for commit links (GitHub Enterprise: https://<host>)
    # Branches and JIRA
    branches: CsvList = ["development", "hotfix/*", "release/*"]
    jira_ac_field: str | None = None
    jira_ac_heading: str = "Acceptance Criteria"
    jira_cache_ttl_seconds: int = 600
    # LLM
    llm_provider: Literal["anthropic"] = "anthropic"
    llm_model: str = "claude-sonnet-5-5"
    llm_base_url: str | None = None
    llm_daily_token_budget: int = 2_000_000
    llm_max_input_tokens: int = 60_000
    llm_max_output_tokens: int = 4096
    # Files and pushes
    files_exclude: CsvList = [
        "**/package-lock.json", "**/*.min.js", "**/generated/**",
        "**/dist/**", "**/target/**", "**/node_modules/**",
    ]
    files_priority: CsvList = [
        "**/application*.yml", "**/application*.yaml", "**/application*.properties",
        "**/*envoy*.y*ml", "**/*gateway*.y*ml", "**/helm/**", "**/k8s/**",
        "**/Dockerfile", "**/environment*.ts", "**/proxy.conf.json",
    ]
    max_commits_per_push: int = 200
    max_chunks_per_commit: int = 3
    # Release notes and docs
    github_api_url: str = "https://api.github.com"
    github_token: SecretStr | None = None
    nexus_maven_repo: str = "maven-public"
    nexus_npm_repo: str = "npm-group"
    docs_max_chars: int = 40_000
    usage_context_max_chars: int = 20_000
    library_profile_max_chars: int = 30_000
    # SMTP
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    # Reports and portal
    report_debounce_seconds: int = 300
    portal_auth_header: str | None = None
    # Runtime
    timezone: str = "UTC"
    http_timeout_seconds: float = 30.0
    job_lease_seconds: int = 900
    retry_backoff_seconds: CsvIntList = [30, 120, 600]
    worker_poll_seconds: float = 2.0
    log_level: str = "INFO"
    port: int = Field(8080, validation_alias="PORT")

    @field_validator(
        "jira_project_keys", "digest_recipients", "branches", "files_exclude",
        "files_priority", "retry_backoff_seconds", mode="before",
    )
    @classmethod
    def _split_csv(cls, v):
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def _cross_field_checks(self):
        problems = [(f, str(getattr(self, f)), f"file not found: {getattr(self, f)}")
                    for f in _FILE_FIELDS if not getattr(self, f).is_file()]
        required = _PROVIDER_FIELDS[self.source_provider]
        problems += [(f, None, f"required when SCOPE_SOURCE_PROVIDER={self.source_provider}")
                     for f in required if getattr(self, f) is None]
        if problems:
            raise ValidationError.from_exception_data(
                type(self).__name__,
                [InitErrorDetails(type="value_error", loc=(f,), input=value, ctx={"error": message})
                 for f, value, message in problems],
            )
        return self


def _env_name(loc: tuple) -> str:
    name = str(loc[0]) if loc else "SETTINGS"
    return name if name == "PORT" else f"SCOPE_{name.upper()}"


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        for err in exc.errors():
            reason = err["msg"].removeprefix("Value error, ")
            print(f"{_env_name(err['loc'])}: {reason}", file=sys.stderr)
        raise SystemExit(2) from exc
