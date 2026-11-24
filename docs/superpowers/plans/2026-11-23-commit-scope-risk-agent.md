# Commit Scope & Risk Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A twelve-factor service that analyzes every commit pushed to `development`, `hotfix/*` and `release/*` on Bitbucket Data Center. It flags:
- risky runtime changes, especially dependency upgrades and library swaps
- out-of-scope work, judged against JIRA Cloud
- ticket gaps

It emails the author and leads for each push, and sends PMO a daily digest. It never blocks anything.

**Architecture:** One container image runs three process types:
- **`web`** verifies Bitbucket webhooks, enqueues jobs, and records "useful / noise" ratings.
- **`worker`** claims jobs from Postgres and runs the analysis pipeline. It can scale horizontally.
- **`digest`** is a one-off process triggered by the platform scheduler.

The code is layered so each module has a single responsibility:
- **Adapters** (Bitbucket, JIRA, Nexus, GitHub, LLM, SMTP) only translate HTTP or SMTP calls. They contain no business rules.
- **Domain rules** (version maths, dependency diffing, swap detection, ticket gaps, file selection, routing) are pure code with no I/O.
- **Services** (dependency analysis, code analysis, pipeline, notifier, worker) orchestrate the adapters and rules.
- **Repositories** persist data, one class per aggregate.
- **`composition.py`** is the only place that builds objects.

All configuration comes from environment variables.

**Tech Stack:** Python 3.12 and uv (lockfile), FastAPI + uvicorn, httpx, SQLAlchemy 2 + psycopg 3 (PostgreSQL; SQLite in tests), pydantic v2 + pydantic-settings, PyYAML (mounted data files only), Jinja2, anthropic SDK, typer, structlog. Tests: pytest and respx. Runtime: Docker and docker-compose (postgres, mailpit).

**Spec:** `docs/superpowers/specs/2026-11-23-commit-scope-risk-agent-design.md`. Read §5.1 first; it holds the twelve-factor and single-responsibility rules.

## Global Constraints

- **Config:** read only through `Settings` (Task 1). No module reads `os.environ`. There are no hard-coded URLs, emails, secrets, thresholds or branch names outside `Settings` defaults. Every setting appears in `.env.example`.
- **Structured data files** (routing, dependency hints) are loaded only from the paths `SCOPE_ROUTING_FILE` and `SCOPE_DEPENDENCY_HINTS_FILE`. Nothing is baked into the image.
- **Backing services:** the database, SMTP, LLM, Bitbucket, JIRA, Nexus and GitHub are attached only via `Settings` URLs and credentials.
- **Logs:** JSON lines to stdout via `structlog`. No log files, no `print` outside CLI output.
- **Processes:** stateless. The web server binds `0.0.0.0:$PORT`. The worker handles SIGTERM by finishing or releasing its current job.
- **Single responsibility:**
  - Adapters contain no business rules.
  - Domain modules perform no I/O.
  - Only `composition.py` constructs concrete adapters.
  - A class whose description needs "and" is split.
- **Report-only:** nothing blocks a push or merge, and nothing is written to Bitbucket or JIRA.
- **Branches analyzed:** from `SCOPE_BRANCHES`, default `development,hotfix/*,release/*`.
- **Bitbucket DC:** REST `1.0`, Bearer token. The webhook HMAC arrives as `X-Hub-Signature: sha256=<hex>`.
- **JIRA Cloud:** REST v3, basic auth (email + API token). The Epic comes from `parent`. Rich text is ADF.
- **Severity order:** `high` > `medium` > `low` > `info`. `info` goes to the digest only.
- **Unknown is not safe:** missing release notes produce a `behavior_unknown` finding.
- **LLM access** goes only through the `LLMClient` protocol, and only `llm/anthropic_client.py` imports `anthropic`.
- **Budget exhaustion** defers the job to the next day, counted in the digest. Nothing is dropped.
- **Tone:** emails are neutral and "for review", and every finding says why it was flagged.

## Review Focus

1. **Duplicate deliveries and the same commit on several branches:** each commit is analyzed once, with at most one email. *Tasks 2, 12, 15.*
2. **Worker crash mid-job, or SIGTERM during deploy:** the job is reclaimed after `SCOPE_JOB_LEASE_SECONDS` and finished by another worker, not lost and not run twice concurrently. *Tasks 2, 15.*
3. **LLM invalid JSON or API error:** one retry, then a recorded error. The worker survives, and errors are counted in the digest. *Tasks 9, 15.*
4. **Release notes unavailable** (GitHub 404 or rate limit, no changelog): emits `behavior_unknown`, never silence. *Tasks 8, 10.*
5. **Missing or invalid config at startup** (an unset required env var, a routing file path that doesn't exist, a malformed list): the process exits non-zero immediately with a message naming the variable. It must not fail later in the middle of a job. *Task 1.*

---

## File Structure

```
pyproject.toml, uv.lock, Dockerfile, docker-compose.yml, .env.example, .gitignore, README.md
deploy/routing.example.yaml, deploy/dependency-hints.example.yaml
src/scope_agent/
  settings.py                 Settings (env only) + validation
  logging.py                  structlog JSON-to-stdout setup
  domain/
    models.py                 Finding, CommitData, FileChange, TicketContext, DependencyChange, LibrarySummary, PushJob, Severity, FindingType
    versions.py               version parsing/comparison (pure)
    maven_diff.py             pom before/after → DependencyChange (pure)
    npm_diff.py               package.json / lockfile before/after → DependencyChange (pure)
    dependency_files.py       dispatch FileChange → extractor (pure)
    swaps.py                  swap detection (pure)
    jira_keys.py              key extraction (pure)
    adf.py                    ADF → text, AC extraction (pure)
    ticket_gaps.py            ticket gap rules (pure)
    file_selection.py         exclude/prioritise/unified diff (pure)
    chunking.py               token-budget chunking (pure)
    routing.py                RoutingConfig + reviewers/recipients (pure)
    dependency_hints.py       DependencyHints model (pure)
  config_files.py             load routing + dependency-hints YAML from paths
  persistence/
    database.py               engine, metadata, create_all
    jobs.py                   JobQueue
    commits.py                CommitLedger
    findings.py               FindingRepository (findings, summaries, errors)
    ratings.py                RatingRepository
    cache.py                  KeyValueCache
    usage.py                  TokenUsageMeter
    meta.py                   MetaStore
  adapters/
    http.py                   shared httpx client factory with retry
    bitbucket.py              BitbucketClient
    jira.py                   JiraClient
    nexus.py                  NexusClient
    github.py                 GitHubClient
    web_pages.py              WebPageFetcher (HTML → text)
    smtp.py                   SmtpSender (EmailSender protocol)
  llm/
    base.py                   LLMClient protocol, LLMResult, LLMOutputError, BudgetExceeded
    schemas.py                LLMFinding, DependencyVerdict, CodeVerdict, RISK_CATEGORIES
    anthropic_client.py       AnthropicClient
    budget.py                 BudgetGuard
    prompts.py + prompts/*.j2 render_prompt
    factory.py                build_llm(settings, meter)
  services/
    source_resolver.py        library → SourceRepo
    release_notes.py          ReleaseNotesService (GitHub/changelog/Bitbucket + cache)
    library_docs.py           LibraryDocsService
    usage_context.py          how our code uses a library
    library_profile.py        behavior profile of a removed in-house library
    dependency_analysis.py    DependencyAnalyzer
    code_analysis.py          CodeAnalyzer
    ticket_resolver.py        keys → TicketContext / not-found
    pipeline.py               PushPipeline
    push_notifier.py          PushNotifier
    digest.py                 DigestService
  notify/
    rating_links.py           token + URL
    push_email.py             render push email
    digest_email.py           render digest email
    templates/*.j2
  web/
    app.py                    create_app
    webhook.py                webhook router
    rating.py                 rating router
  worker/
    retry_policy.py           RetryPolicy
    worker.py                 Worker (claim → run → notify → complete)
  composition.py              Container / build_container (only place adapters are constructed)
  cli.py                      typer: web, worker, digest, migrate, analyze, backtest, report
tests/ mirrors src/; fixtures in tests/fixtures/
```

---

### Task 1: Scaffold, environment settings, logging and domain models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/scope_agent/__init__.py`, `src/scope_agent/settings.py`, `src/scope_agent/logging.py`, `src/scope_agent/domain/__init__.py`, `src/scope_agent/domain/models.py`
- Test: `tests/test_settings.py`, `tests/test_logging.py`, `tests/domain/test_models.py`

**Interfaces:**
- Produces: `Settings(BaseSettings)` with `env_prefix="SCOPE_"`. List-typed fields parse from **comma-separated** strings via a `field_validator(mode="before")`. `PORT` is read without a prefix via `validation_alias="PORT"`.

  Required fields (no default):
  - Bitbucket: `bitbucket_url`, `bitbucket_token`, `webhook_secret`
  - JIRA: `jira_url`, `jira_email`, `jira_token`, `jira_project_keys: list[str]`
  - LLM: `llm_api_key`
  - Nexus: `nexus_url`, `nexus_user`, `nexus_password`
  - SMTP and links: `smtp_host`, `smtp_from`, `public_base_url`, `rating_secret`
  - Storage and recipients: `database_url`, `digest_recipients: list[str]`
  - Data files: `routing_file: Path`, `dependency_hints_file: Path`

  Defaults:
  - Branches and JIRA: `branches=["development","hotfix/*","release/*"]`, `jira_ac_field: str | None=None`, `jira_ac_heading="Acceptance Criteria"`, `jira_cache_ttl_seconds=600`
  - LLM: `llm_provider: Literal["anthropic"]="anthropic"`, `llm_model="claude-sonnet-5-5"`, `llm_base_url: str | None=None`, `llm_daily_token_budget=2_000_000`, `llm_max_input_tokens=60_000`, `llm_max_output_tokens=4096`
  - Files and pushes:
    - `files_exclude`, with default `["**/package-lock.json","**/*.min.js","**/generated/**","**/dist/**","**/target/**","**/node_modules/**"]`
    - `files_priority`, with default `["**/application*.yml","**/application*.yaml","**/application*.properties","**/*envoy*.y*ml","**/*gateway*.y*ml","**/helm/**","**/k8s/**","**/Dockerfile","**/environment*.ts","**/proxy.conf.json"]`
    - `max_commits_per_push=200`, `max_chunks_per_commit=3`
  - Release notes and docs: `github_api_url="https://api.github.com"`, `github_token: str | None=None`, `nexus_maven_repo="maven-public"`, `nexus_npm_repo="npm-group"`, `docs_max_chars=40_000`, `usage_context_max_chars=20_000`, `library_profile_max_chars=30_000`
  - SMTP: `smtp_port=587`, `smtp_user: str | None=None`, `smtp_password: str | None=None`, `smtp_starttls=True`
  - Runtime: `timezone="UTC"`, `http_timeout_seconds=30.0`, `job_lease_seconds=900`, `retry_backoff_seconds: list[int]=[30,120,600]`, `worker_poll_seconds=2.0`, `log_level="INFO"`, `port=8080`

  The secrets are typed `SecretStr`: tokens, passwords, keys and `webhook_secret`/`rating_secret`. A `model_validator` checks that `routing_file` and `dependency_hints_file` exist.
- Produces: `load_settings() -> Settings`. On `ValidationError` it writes one line per bad or missing variable to stderr (`SCOPE_<NAME>: <reason>`) and raises `SystemExit(2)`.
- Produces: `configure_logging(level: str) -> None`, which sets structlog to emit JSON to stdout with an ISO timestamp, the level and the event.
- Produces (`domain/models.py`):
  - `Severity` (str enum `high|medium|low|info`) with a `rank` property (3…0)
  - `FindingType` (`dependency_behavior_change|risky_change|out_of_scope|ticket_gap`)
  - `Finding`: `id` (uuid4 str default), `repo` (`"PROJ/slug"`), `commit`, `author_email: str | None`, `jira_keys: list[str]`, `type`, `category: str`, `severity`, `title`, `explanation`, `evidence: list[str]`, `files: list[str]`, `suggested_reviewers: list[str]`, `created_at`
  - `FileChange(path, status: Literal["added","modified","deleted"], before: str | None, after: str | None)`
  - `CommitData(repo, sha, author_name, author_email: str | None, message, parents: list[str], files: list[FileChange])` with an `is_merge` property
  - `TicketContext(key, summary, description, issue_type, status, acceptance_criteria: str | None, components, labels, epic_key: str | None, epic_summary: str | None, epic_description: str | None, linked_titles: list[str])`
  - `DependencyChange(ecosystem: Literal["maven","npm"], name, old_version: str | None, new_version: str | None, kind: Literal["added","removed","upgraded","downgraded","parent","bom","transitive"], file: str)`
  - `LibrarySummary(library, kind: Literal["upgrade","new","swap"], summary, links: list[str])`
  - `PushJob(repo, branch, from_hash: str | None, to_hash, pushed_by: str | None)`

- [ ] **Step 1: Write failing tests**

```python
REQUIRED = {"SCOPE_BITBUCKET_URL": "https://bb.corp", "SCOPE_BITBUCKET_TOKEN": "t", "SCOPE_WEBHOOK_SECRET": "s",
            "SCOPE_JIRA_URL": "https://corp.atlassian.net", "SCOPE_JIRA_EMAIL": "svc@corp", "SCOPE_JIRA_TOKEN": "j",
            "SCOPE_JIRA_PROJECT_KEYS": "PAY,PLAT", "SCOPE_LLM_API_KEY": "k", "SCOPE_NEXUS_URL": "https://nexus.corp",
            "SCOPE_NEXUS_USER": "u", "SCOPE_NEXUS_PASSWORD": "p", "SCOPE_SMTP_HOST": "smtp", "SCOPE_SMTP_FROM": "agent@corp",
            "SCOPE_PUBLIC_BASE_URL": "https://agent.corp", "SCOPE_RATING_SECRET": "r",
            "SCOPE_DATABASE_URL": "sqlite://", "SCOPE_DIGEST_RECIPIENTS": "pmo@corp"}

def test_settings_from_env_with_defaults(monkeypatch, data_files):   # data_files fixture sets the two file paths to tmp files
    for k, v in REQUIRED.items(): monkeypatch.setenv(k, v)
    s = load_settings()
    assert s.jira_project_keys == ["PAY", "PLAT"]
    assert s.branches == ["development", "hotfix/*", "release/*"]
    assert s.retry_backoff_seconds == [30, 120, 600]
    assert s.bitbucket_token.get_secret_value() == "t"

def test_list_env_override(monkeypatch, data_files):
    ...; monkeypatch.setenv("SCOPE_BRANCHES", "main, release/*")
    assert load_settings().branches == ["main", "release/*"]

def test_missing_required_exits_naming_variable(monkeypatch, data_files, capsys):
    ...  # all REQUIRED except SCOPE_JIRA_TOKEN
    with pytest.raises(SystemExit) as e: load_settings()
    assert e.value.code == 2 and "SCOPE_JIRA_TOKEN" in capsys.readouterr().err

def test_missing_routing_file_exits(monkeypatch, capsys):
    ...  # SCOPE_ROUTING_FILE=/nonexistent.yaml
    with pytest.raises(SystemExit): load_settings()
    assert "SCOPE_ROUTING_FILE" in capsys.readouterr().err

def test_port_unprefixed(monkeypatch, data_files):
    ...; monkeypatch.setenv("PORT", "9000"); assert load_settings().port == 9000

def test_logging_emits_json(capsys):
    configure_logging("INFO"); structlog.get_logger().info("hello", repo="PAY/x")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "hello" and line["repo"] == "PAY/x" and line["level"] == "info"

def test_severity_rank_and_is_merge(): ...  # high>medium>low>info; parents ["1","2"] → is_merge
```

- [ ] **Step 2: Run** `uv run pytest tests/test_settings.py tests/test_logging.py tests/domain -v`. Expected: FAIL (import errors).
- [ ] **Step 3: Implement** per the Interfaces. In `.env.example`, list every `SCOPE_*` field and `PORT`: required ones blank, defaults shown, one comment line each. Use `pyproject.toml` with a `src` layout, a script `scope-agent = "scope_agent.cli:app"`, and dev dependencies `pytest`, `respx`.
- [ ] **Step 4: Run** `uv sync && uv run pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: scaffold, env-only settings, json logging and domain models`

---

### Task 2: Persistence: database and repositories

**Files:**
- Create: `src/scope_agent/persistence/{__init__,database,jobs,commits,findings,ratings,cache,usage,meta}.py`
- Test: `tests/persistence/test_*.py` (one per repository; `conftest.py` provides `db = Database("sqlite://")` with `create_all()`)

**Interfaces:**
- Consumes: domain models (Task 1)
- Produces: `Database(url: str)` exposing `.engine` and `.session()` (a context manager, commit on exit), plus `create_all()`. The tables are `jobs`, `analyzed_commits` (PK repo+sha), `findings` (lists as JSON), `library_summaries`, `analysis_errors`, `ratings` (PK finding_id), `cache`, `token_usage` (PK day) and `meta`.
- Each repository below takes `(db: Database, clock: Callable[[], datetime] = utcnow)`.

  **`JobQueue`**
  - `enqueue(job: PushJob, dedup_key: str) -> int | None`: returns `None` when the key exists. The key is `f"{repo}:{branch}:{to_hash}"`.
  - `claim(lease_seconds: int) -> tuple[int, PushJob, int] | None`: returns the id, the job and the attempts. It picks the oldest job that is either:
    - `queued` or `deferred` with `not_before <= now`, or
    - `running` with `lease_until < now` (crash recovery).

    It sets `running`, `lease_until = now + lease`, and `attempts += 1`. On PostgreSQL it uses `SELECT … FOR UPDATE SKIP LOCKED`; on SQLite, a plain select-then-update.
  - `complete(id)`, `retry(id, at: datetime, error: str)`, `fail(id, error: str)`, `defer(id, until: datetime)`, `release(id)` (back to `queued`, `attempts -= 1`; used on SIGTERM)
  - `count_by_status() -> dict[str, int]`

  **`CommitLedger`**: `is_analyzed(repo, sha) -> bool`, `mark_analyzed(repo, shas: list[str])`

  **`FindingRepository`**
  - `save(job_id: int, findings: list[Finding], summaries: dict[str, list[LibrarySummary]], errors: list[tuple[str, str | None, str]])` writes everything in one transaction. Each error is `(repo, commit, message)`.
  - `get(finding_id) -> Finding | None`, `since(start: datetime, end: datetime) -> list[Finding]`, `summaries_for(job_id) -> dict[str, list[LibrarySummary]]`, `errors_between(start, end) -> int`

  **`RatingRepository`**: `save(finding_id, value: Literal["useful","noise"])` (upsert), `report() -> list[dict]` (rows `{type, severity, useful, noise}`)

  **`KeyValueCache`**: `get(namespace, key) -> str | None`, `put(namespace, key, value, ttl_seconds: int | None)` (None means permanent)

  **`TokenUsageMeter`**: `add(day: date, tokens: int)`, `used(day: date) -> int`

  **`MetaStore`**: `get(key) -> str | None`, `set(key, value)`
- `PushPipeline` (Task 12) needs to save findings and mark commits atomically. To support that, `FindingRepository.save` and `CommitLedger.mark_analyzed` accept an optional `session` argument, and `Database.session()` can be shared across both calls.

- [ ] **Step 1: Write failing tests**

```python
def test_enqueue_dedups(jobs, job):
    assert jobs.enqueue(job, "PAY/r:development:abc") is not None
    assert jobs.enqueue(job, "PAY/r:development:abc") is None

def test_claim_skips_future_deferred(jobs, job, clock):
    jid = jobs.enqueue(job, "k"); jobs.claim(60); jobs.defer(jid, until=clock.now + timedelta(hours=1))
    assert jobs.claim(60) is None
    clock.advance(hours=2); assert jobs.claim(60)[0] == jid

def test_expired_lease_is_reclaimed(jobs, job, clock):            # Review Focus 2
    jid = jobs.enqueue(job, "k"); jobs.claim(60)
    assert jobs.claim(60) is None                                  # still leased
    clock.advance(seconds=61); claimed = jobs.claim(60)
    assert claimed[0] == jid and claimed[2] == 2

def test_release_returns_job_without_counting_attempt(jobs, job): ...  # claim → release → claim gives attempts == 1

def test_findings_save_atomic_with_ledger(db, finding):
    with db.session() as s:
        FindingRepository(db).save(1, [finding], {"abc": []}, [("PAY/r", "abc", "boom")], session=s)
        CommitLedger(db).mark_analyzed("PAY/r", ["abc"], session=s)
    assert CommitLedger(db).is_analyzed("PAY/r", "abc") and FindingRepository(db).get(finding.id)

def test_rating_upsert_and_report(db, finding): ...   # useful then noise → [{"type":…, "severity":…, "useful":0, "noise":1}]
def test_cache_ttl(db, clock): ...                    # put ttl=1 → get == value; advance 2s → None
def test_token_usage_sums_per_day(db): ...            # add 100 + 50 → used == 150
```

`clock` is a fixture: an object with `.now`, `.advance(**timedelta_kwargs)`, and is callable, returning `.now`.

Add `test_claim_postgres_skip_locked`. It is skipped unless `SCOPE_TEST_DATABASE_URL` is set. Two concurrent sessions each claim a different job.
- [ ] **Step 2: Run** `uv run pytest tests/persistence -v`. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: database and per-aggregate repositories with leased job queue`

---

### Task 3: Mounted data files: routing and dependency hints

**Files:**
- Create: `src/scope_agent/domain/routing.py` (models only for now), `src/scope_agent/domain/dependency_hints.py`, `src/scope_agent/config_files.py`, `deploy/routing.example.yaml`, `deploy/dependency-hints.example.yaml`
- Test: `tests/test_config_files.py`

**Interfaces:**
- Produces:
  - `RepoRouting(leads: list[str] = [], paths: dict[str, list[str]] = {})` and `RoutingConfig(repos: dict[str, RepoRouting] = {}, categories: dict[str, list[str]] = {}, default_leads: list[str])`. `default_leads` is required and must be non-empty.
  - `DependencyHints(library_docs: dict[str, list[str]] = {}, internal_repos: dict[str, str] = {}, known_swaps: list[tuple[str, str]] = [])`. `internal_repos` maps a library name to `"PROJ/slug"`.
  - `load_routing(path: Path) -> RoutingConfig` and `load_dependency_hints(path: Path) -> DependencyHints`. Both raise `ConfigFileError(path, reason)` on bad YAML or a schema error.

- [ ] **Step 1: Write failing tests**
  - `test_load_routing_example`: it parses `deploy/routing.example.yaml`, which mirrors the example in spec §6.7.
  - `test_routing_requires_default_leads`
  - `test_load_hints_example`
  - `test_bad_yaml_raises_config_file_error_with_path`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use `yaml.safe_load` and pydantic validation.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: routing and dependency-hints data files loaded from env-provided paths`

---

### Task 4: Shared HTTP factory and Bitbucket adapter (webhook parsing + REST client)

**Files:**
- Create: `src/scope_agent/adapters/{__init__,http,bitbucket}.py`, `tests/fixtures/refs_changed.json`
- Test: `tests/adapters/test_http.py`, `tests/adapters/test_bitbucket.py` (respx)

**Interfaces:**
- Produces: `build_http_client(base_url: str, timeout: float, auth: httpx.Auth | None = None, headers: dict | None = None) -> httpx.Client`. It retries 5xx and 429 up to 3 tries with exponential backoff (0.5, 1, 2 s) via a transport wrapper. It does not retry 4xx except 429.
- Produces (pure functions in `adapters/bitbucket.py`, kept with the adapter because they concern the wire format):
  - `verify_signature(secret: str, body: bytes, header: str | None) -> bool`: constant-time comparison against `"sha256=" + hmac_sha256_hex`.
  - `parse_refs_changed(payload: dict, branch_globs: list[str]) -> list[PushJob]`.
    - Keeps only `ref.type == "BRANCH"` and `type != "DELETE"`, with `fnmatchcase(ref.displayId, glob)` for some glob.
    - An all-zero `fromHash` becomes `None`.
    - `repo` is `f"{project.key}/{slug}"`.
- Produces: `BitbucketClient(http: httpx.Client)`. Every method takes `repo="PROJ/slug"`.
  - `commits_in_push(job, limit) -> list[str]`
    - Returns the SHAs oldest-first, newest `limit` kept.
    - Calls `GET /rest/api/1.0/projects/{p}/repos/{r}/commits?until={to}&since={from}` and follows `isLastPage`/`nextPageStart`.
    - If `from_hash` is None, `since` is `latestCommit` from `GET …/branches/default`.
  - `get_commit(repo, sha) -> CommitData`
    - Author, message and parents come from `GET …/commits/{sha}`.
    - Changed paths come from `GET …/commits/{sha}/changes`, paged. `ADD`→`added`, `DELETE`→`deleted`, anything else→`modified` using the new path.
    - Contents come from `GET …/raw/{path}?at={sha}` (after) and `?at={parents[0]}` (before). A 404 means `None`.
    - Binary content (a NUL byte in the first 8 KB) or anything over 1 MB is stored as `before=after=None`.
  - `get_file(repo, path, at) -> str | None`
  - `list_files(repo, at, globs, limit=50) -> list[str]`
  - `default_branch_head(repo) -> str`
  - `list_tags(repo) -> list[str]`
  - `commit_messages_between(repo, since_ref, until_ref, limit=200) -> list[str]`

The fixture `refs_changed.json` has repo `PAY/payments-service` and five changes:
1. `UPDATE` on `development`
2. `ADD` on `release/1.4`, with an all-zero `fromHash`
3. `DELETE` on `hotfix/x`
4. `UPDATE` on `feature/PAY-1`
5. a `TAG` change

- [ ] **Step 1: Write failing tests**
  - `test_http_retries_503_then_succeeds`
  - `test_http_does_not_retry_404`
  - `test_verify_signature_ok_bad_missing`
  - `test_parse_filters_branches_tags_deletes`: expects `[("PAY/payments-service","development"),("PAY/payments-service","release/1.4")]`, and the second job has `from_hash is None`.
  - `test_commits_in_push_paginates_oldest_first`
  - `test_commits_in_push_new_branch_uses_default_head`
  - `test_get_commit_builds_file_changes`
  - `test_get_commit_marks_binary_and_oversize`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: http factory and bitbucket data center adapter`

---

### Task 5: JIRA: key extraction, ADF, adapter and ticket resolver

**Files:**
- Create: `src/scope_agent/domain/jira_keys.py`, `src/scope_agent/domain/adf.py`, `src/scope_agent/adapters/jira.py`, `src/scope_agent/services/__init__.py`, `src/scope_agent/services/ticket_resolver.py`, fixtures `jira_issue.json`, `jira_story_parent.json`, `jira_epic.json`
- Test: `tests/domain/test_jira_keys.py`, `tests/domain/test_adf.py`, `tests/adapters/test_jira.py`, `tests/services/test_ticket_resolver.py`

**Interfaces:**
- Produces: `extract_keys(message: str, branch: str | None, project_keys: list[str]) -> list[str]`. It is ordered and unique, uses regex `\b([A-Z][A-Z0-9]+-\d+)\b`, and keeps only configured projects. The branch is used only if the message has no key.
- Produces: `adf_to_text(doc: dict | None) -> str`. Headings render as `## text`, list items as `- text`, and paragraphs as lines.
- Produces: `extract_ac(text: str, heading: str) -> str | None`. It finds the first line matching `^#*\s*{heading}\s*:?$` (case-insensitive) and returns the trimmed lines up to the next line starting with `#`, or to the end.
- Produces: `JiraClient(http: httpx.Client)` with `get_issue(key: str, extra_fields: list[str]) -> dict | None`. It calls `GET /rest/api/3/issue/{key}?fields=summary,description,issuetype,status,components,labels,parent,issuelinks[,extra]`, and returns `None` on 404 or 403. It is a raw adapter with no business rules.
- Produces: `TicketResolver(jira: JiraClient, cache: KeyValueCache, ac_field: str | None, ac_heading: str, cache_ttl: int)` with `resolve(key) -> TicketContext | None`.
  - **Epic:** walk `parent` until `issuetype.name == "Epic"`, at most 2 hops.
  - **ACs:** taken from `ac_field` if it's set (ADF or plain string), otherwise `extract_ac(adf_to_text(description), ac_heading)`.
  - **Cache:** namespace `jira`, with `cache_ttl`.

- [ ] **Step 1: Write failing tests**

```python
def test_extract_keys_conventional_commit():
    assert extract_keys("feat(PAY-12): add flag client\n\nRefs: PAY-13", None, ["PAY"]) == ["PAY-12", "PAY-13"]
def test_extract_keys_ignores_unknown_projects():
    assert extract_keys("fix: UTF-8 handling", None, ["PAY"]) == []
def test_extract_keys_branch_fallback():
    assert extract_keys("fix: typo", "feature/PAY-99-typo", ["PAY"]) == ["PAY-99"]
def test_adf_to_text():
    assert adf_to_text(ADF_FIXTURE) == "## Acceptance Criteria\n- flags load on startup\n- falls back to defaults"
def test_extract_ac_heading_and_plain_label():
    assert extract_ac("Intro\n## Acceptance Criteria\n- a\n- b\n## Notes\nx", "Acceptance Criteria") == "- a\n- b"
    assert extract_ac("Intro\nAcceptance criteria:\n- a", "Acceptance Criteria") == "- a"
```

The remaining tests:
- `test_jira_client_returns_none_on_404`
- `test_resolver_epic_direct_parent`
- `test_resolver_subtask_walks_to_epic`
- `test_resolver_uses_ac_field`
- `test_resolver_caches`: the second resolve makes no HTTP call.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: jira key extraction, adf parsing, jira adapter and ticket resolver`

---

### Task 6: Versions and dependency diffing (Maven, npm, dispatch)

**Files:**
- Create: `src/scope_agent/domain/{versions,maven_diff,npm_diff,dependency_files}.py`, fixtures `pom_before.xml`, `pom_after.xml`, `package_before.json`, `package_after.json`, `lock_before.json`, `lock_after.json`
- Test: `tests/domain/test_versions.py`, `test_maven_diff.py`, `test_npm_diff.py`, `test_dependency_files.py`

**Interfaces:**
- Produces:
  - `version_key(v) -> tuple`: a pre-release qualifier sorts before its release.
  - `is_major_bump(old, new) -> bool`: true when the first numeric component changes, or the second when the version is `0.x`.
  - `versions_between(all, old, new) -> list[str]`: those with `old < v <= new`, ascending.
  - `maven_changes(path, before, after) -> list[DependencyChange]`
  - `npm_changes(path, before, after) -> list[DependencyChange]`
  - `dependency_changes(fc: FileChange) -> list[DependencyChange]`: dispatches on the basename (`pom.xml`, `package.json` or `package-lock.json`); anything else returns `[]`.

- [ ] **Step 1: Write failing tests**

```python
def test_versions():
    assert version_key("1.10.0") > version_key("1.9.3") and version_key("2.0.0-rc1") < version_key("2.0.0")
    assert versions_between(["0.7.0","0.8.0","0.8.1","1.0.0"], "0.7.0", "0.8.1") == ["0.8.0","0.8.1"]
    assert is_major_bump("1.4.2","2.0.0") and is_major_bump("0.7.0","0.8.0") and not is_major_bump("1.4.2","1.5.0")

def test_maven_changes(before, after):
    got = {(c.name, c.kind, c.old_version, c.new_version) for c in maven_changes("svc/pom.xml", before, after)}
    assert got == {
        ("com.corp:flag-util", "removed", "1.3.0", None),
        ("dev.openfeature.contrib.providers:flagd", "added", None, "0.8.1"),
        ("com.squareup.okhttp3:okhttp", "upgraded", "4.9.0", "4.12.0"),          # via ${okhttp.version}
        ("org.springframework.boot:spring-boot-starter-parent", "parent", "3.2.0", "3.3.0"),
        ("com.corp:platform-bom", "bom", "1.0.0", "2.0.0"),                        # dependencyManagement scope=import
    }

def test_npm_changes(pkg_before, pkg_after, lock_before, lock_after):
    assert ("@openfeature/flagd-web-provider", "added", None, "0.6.0") in tuples(npm_changes("web/package.json", pkg_before, pkg_after))
    lock = npm_changes("web/package-lock.json", lock_before, lock_after)
    assert ("rxjs", "transitive") in {(c.name, c.kind) for c in lock}      # 6.6.7 → 7.8.1, not a direct dep
    assert not any(c.name == "tslib" for c in lock)                         # minor transitive ignored

def test_dispatch(): ...  # FileChange("a/pom.xml") → maven; "README.md" → []
```

Also add:
- `test_maven_unresolved_property_kept_raw`
- `test_maven_new_file_all_added`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Maven:** parse with `xml.etree.ElementTree`, using the POM 4.0.0 namespace. Resolve `${x}` from `<properties>` and `${project.version}`.
  - **npm, `package.json`:** strip the leading `^~>=<` from version ranges.
  - **npm, lockfile:** read v2/v3 `packages["node_modules/<name>"]`. Report top-level non-direct deps only when `is_major_bump`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: version utilities and maven/npm dependency diffing`

---

### Task 7: Swap detection

**Files:**
- Create: `src/scope_agent/domain/swaps.py`
- Test: `tests/domain/test_swaps.py`

**Interfaces:**
- Consumes: `DependencyChange`, `CommitData`, `DependencyHints.known_swaps`
- Produces:
  - `Swap(removed, added, reason: Literal["configured","name_overlap","import_change"])`
  - `detect_swaps(changes, commits, known_swaps) -> list[Swap]`

  It pairs a removed and an added dependency of the same ecosystem; the first rule that matches wins.
  1. **Configured:** the pair is listed in `known_swaps`.
  2. **Name overlap:** they share a non-generic token. Split names on `[-.:_/@]` and lower-case them. These tokens are generic and ignored: `com, org, io, dev, net, java, js, core, client, lib, util, utils, api, sdk, contrib, providers, provider, starter, common, web, corp`.
  3. **Import change:** a changed file's removed lines import the removed library's prefix and its added lines import the added library's prefix.
     - **Prefixes:** for Maven, either the groupId or the groupId plus the artifactId with dashes removed; for npm, the package name.
     - **Import lines:** lines starting with `import ` or containing `from '`.

- [ ] **Step 1: Write failing tests**
  - `test_no_swap_without_evidence`: `com.corp:flag-util` and `…providers:flagd` with no imports and no config give `[]`.
  - `test_swap_by_import_change`: `Flags.java` changes `import com.corp.flagutil.FlagClient;` to `import dev.openfeature.contrib.providers.flagd.FlagdProvider;`, giving reason `import_change`.
  - `test_swap_configured`
  - `test_swap_name_overlap_npm`: `ngx-feature-toggle` and `@openfeature/feature-toggle-web`.
  - `test_no_swap_across_ecosystems`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use `difflib` on the before/after lines.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: dependency swap detection`

---

### Task 8: External sources: Nexus, GitHub and web adapters; source resolution, release notes and docs services

**Files:**
- Create: `src/scope_agent/adapters/{nexus,github,web_pages}.py`, `src/scope_agent/services/{source_resolver,release_notes,library_docs}.py`
- Test: `tests/adapters/test_nexus.py`, `test_github.py`, `test_web_pages.py`, `tests/services/test_source_resolver.py`, `test_release_notes.py`, `test_library_docs.py`

**Interfaces:**
- Produces, adapters only, raw and with no rules:
  - `NexusClient(http, maven_repo, npm_repo)`:
    - `maven_pom(group, artifact, version) -> str | None` (`GET /repository/{maven_repo}/{group/path}/{artifact}/{version}/{artifact}-{version}.pom`)
    - `npm_metadata(name) -> dict | None` (`GET /repository/{npm_repo}/{name}`)
  - `GitHubClient(http)`:
    - `releases(owner_repo, max_pages=5) -> list[dict] | None`: `None` on 403, 404 or 429.
    - `file(owner_repo, path) -> str | None` (contents API, raw media type)
    - `readme(owner_repo, subpath: str | None) -> str | None`
  - `WebPageFetcher(http)` with `text(url) -> str | None`: HTML stripped to text via `html.parser`.
- Produces, services:
  - `SourceRepo(kind: Literal["github","bitbucket","unknown"], ref: str, subpath: str | None)`
  - `SourceResolver(nexus, hints: DependencyHints, bitbucket_host: str)` with `resolve(change) -> SourceRepo`.
    - It checks `hints.internal_repos` first.
    - Then it tries the Maven POM's `<scm><url>` or `<url>`, or for npm `repository.url` plus `repository.directory`.
    - A host equal to `bitbucket_host` means Bitbucket; it parses `/projects/P/repos/r` or `/scm/p/r.git`.
  - `ReleaseNotes(library, entries: list[tuple[str, str]], links: list[str], available: bool)`
  - `ReleaseNotesService(resolver, github, bitbucket, cache)` with `between(change) -> ReleaseNotes`. Results are cached permanently under the key `f"{name}@{old}..{new}"`.
    - **GitHub releases:** read the tag version with the trailing regex `(\d+(?:\.\d+)+(?:[-.][0-9A-Za-z.]+)?)$`. When any tag contains the package hint (the artifactId, or the npm name without its scope), only those tags are used. Keep `versions_between`.
    - **GitHub changelog fallback:** look for `CHANGELOG.md`, `CHANGES.md`, `RELEASE_NOTES.md` (under `subpath` first). Split on headings that contain a version.
    - **Bitbucket:** look for tags `v{ver}`, `{ver}` and `{artifact}-{ver}`. Use the `commit_messages_between` output as a single entry.
    - **Failure:** if nothing is found, or the adapter returns `None`, the result has `available=False`.
  - `LibraryDocs(library, version, text, links)` and `LibraryDocsService(resolver, github, bitbucket, pages, hints, max_chars)` with `docs_for(change) -> LibraryDocs`. The sources are the README (GitHub or the Bitbucket tag) plus `hints.library_docs[name]` pages, truncated to `max_chars`.

- [ ] **Step 1: Write failing tests**
  - `test_resolve_maven_scm_github`: `open-feature/java-sdk-contrib`.
  - `test_resolve_internal_hint_wins`
  - `test_resolve_bitbucket_scm_url`: `https://bitbucket.corp/scm/plat/flag-util.git` gives `("bitbucket","PLAT/flag-util")`.
  - `test_between_monorepo_tags`: tags `…flagd-v0.8.0`, `…flagd-v0.8.1` and `…ofrep-v0.1.0`; for `0.7.0` to `0.8.1` the versions are `["0.8.0","0.8.1"]`.
  - `test_between_changelog_fallback`
  - `test_between_rate_limited_unavailable` *(Review Focus 4)*
  - `test_between_bitbucket_commit_messages`
  - `test_between_cached`
  - `test_docs_readme_plus_hint_page_html_stripped`
  - `test_docs_truncated_to_max_chars`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: nexus/github/web adapters and release-notes/docs services`

---

### Task 9: LLM layer

**Files:**
- Create: `src/scope_agent/llm/{__init__,base,schemas,anthropic_client,budget,prompts,factory}.py`, `src/scope_agent/llm/prompts/{dependency_upgrade,dependency_new,dependency_swap,code_review}.j2`
- Test: `tests/llm/test_anthropic_client.py`, `test_budget.py`, `test_prompts.py`, `test_factory.py`

**Interfaces:**
- Produces (`base.py`): `LLMResult(data: BaseModel, input_tokens: int, output_tokens: int)`; `class LLMClient(Protocol): def analyze(self, system: str, prompt: str, schema: type[T]) -> LLMResult`; `LLMOutputError`, `BudgetExceeded`
- Produces (`schemas.py`):
  - `LLMFinding(type: FindingType, category: str, severity: Severity, title: str, explanation: str, evidence: list[str], files: list[str] = [])`
  - `DependencyVerdict(findings: list[LLMFinding], reviewer_summary: str)`
  - `CodeVerdict(findings: list[LLMFinding], ac_quality: Literal["ok","vague","missing"], ac_quality_reason: str)`
  - `RISK_CATEGORIES`, grouped:
    - runtime behavior: `connection_model`, `connection_pool`, `retries_timeouts`, `caching`, `resource_usage`, `tls_security`, `serialization`, `config_removed`, `concurrency`
    - change type: `schema_migration`, `auth_config`, `external_call`, `feature_flag_defaults`, `infra_config`
    - dependency-specific: `new_dependency`, `platform_upgrade`, `behavior_unknown`, `transitive_major`
- Produces: `AnthropicClient(api_key: str, model: str, max_tokens: int, base_url: str | None)`.
  - It forces a single tool `report`, with `input_schema=schema.model_json_schema()` and `tool_choice={"type":"tool","name":"report"}`.
  - Retries once with the validation error appended, then raises `LLMOutputError`.
  - Calls are logged with tokens only, never the prompt.
- Produces: `BudgetGuard(inner: LLMClient, meter: TokenUsageMeter, daily_budget: int, today: Callable[[], date])`. It implements `LLMClient`, raises `BudgetExceeded` before calling when `used(today) >= budget`, and records the tokens afterwards.
- Produces: `render_prompt(name: str, **ctx) -> str`, using a Jinja2 `PackageLoader("scope_agent.llm","prompts")`.
- Produces: `build_llm(settings, meter) -> LLMClient`, which returns `BudgetGuard(AnthropicClient(...))` for `llm_provider == "anthropic"`. It is the only switch point for a future local provider.
- **Prompt requirements:**
  - **All prompts:** state that the output is for human review, list `RISK_CATEGORIES`, and ask for verbatim evidence.
  - **`dependency_upgrade`:** looks for changed defaults or runtime behavior (spec §6.4 step 3). Severity is raised when our usage relies on defaults. The summary is at most 120 words.
  - **`dependency_new`:** summarizes runtime defaults. Network, IO or concurrency libraries are at least `medium`.
  - **`dependency_swap`:** compares the two sides on connection model, connection count and keep-alive, ports, retries, caching and failure behavior. A connection-model or connection-count difference is `high` with `connection_model`.
  - **`code_review`:** reports `risky_change` and `out_of_scope` findings. Neighboring cleanup is fine; formatting-only or test-only changes are never out of scope. It also rates `ac_quality`.

- [ ] **Step 1: Write failing tests**
  - `test_parses_tool_call`
  - `test_retries_once_then_raises` *(Review Focus 3)*
  - `test_budget_blocks_before_call`
  - `test_budget_records_usage`
  - `test_swap_prompt_lists_categories`
  - `test_factory_wraps_in_budget_guard`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: llm protocol, anthropic client, budget guard, prompts and factory`

---

### Task 10: Dependency analysis service

**Files:**
- Create: `src/scope_agent/services/{usage_context,library_profile,dependency_analysis}.py`
- Test: `tests/services/test_usage_context.py`, `test_library_profile.py`, `test_dependency_analysis.py`

**Interfaces:**
- Produces: `UsageContextBuilder(bitbucket, max_chars)` with `build(commit: CommitData, change: DependencyChange) -> str`. It collects:
  - the unified diffs of changed files that mention the library's import prefix (same prefix rule as Task 7);
  - up to 5 files matching `**/application*.y*ml`, `**/application*.properties` or `**/environment*.ts` at the commit.
- Produces: `LibraryProfileBuilder(resolver, bitbucket, max_chars)` with `profile(change: DependencyChange) -> str | None`. For a Bitbucket source at the old version's tag, it returns the README plus up to 8 files whose path or content matches `poll|schedul|interval|stream|grpc|websocket|http|connect|client`. Otherwise it returns `None`.
- Produces: `DependencyAnalyzer(llm, notes: ReleaseNotesService, docs: LibraryDocsService, usage: UsageContextBuilder, profiles: LibraryProfileBuilder, hints: DependencyHints)` with `analyze(commits: list[CommitData], changes_by_commit: dict[str, list[DependencyChange]]) -> DependencyAnalysis(findings: list[Finding], summaries: dict[str, list[LibrarySummary]], errors: list[str])`.

**Rules per change** (swaps detected across all of the push's changes via `detect_swaps`):

| kind | action | defaults |
|---|---|---|
| in a `Swap` | `dependency_swap` prompt. Old side: `profiles.profile(removed)`, or the removed side's usage context if that's `None`. New side: `docs.docs_for(added)` | attached to the commit that added the library |
| `upgraded` / `downgraded` | `notes.between`. If available, use the `dependency_upgrade` prompt. If not, emit `behavior_unknown` with **no LLM call** | unknown: `medium` if `is_major_bump`, else `low` |
| `parent` / `bom` | emit `platform_upgrade` (`medium`), plus the upgrade analysis | |
| `added` | `docs_for`, then the `dependency_new` prompt | |
| `transitive` | `transitive_major` (`low`), no LLM | |
| `removed`, not a swap | nothing | |

- LLM findings get `type=dependency_behavior_change`, and the notes or doc links are appended to their evidence.
- Every external upgrade, new library or swap produces a `LibrarySummary` from `reviewer_summary`, even when there are no findings.
- An `LLMOutputError` is added to `errors` and analysis continues with the next change.

- [ ] **Step 1: Write failing tests**
  - `test_swap_high_connection_model_on_adding_commit_with_summary`
  - `test_upgrade_without_notes_behavior_unknown_no_llm`: covers both the major and minor cases, with an LLM call count of 0.
  - `test_parent_bump_platform_upgrade`
  - `test_transitive_low_no_llm`
  - `test_added_library_summary_even_without_findings`
  - `test_llm_error_isolated_to_one_change`
  - `test_usage_context_includes_diff_and_app_config_capped`
  - `test_profile_none_for_github_source`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: dependency analysis service with swap, upgrade and new-library rules`

---

### Task 11: Code analysis: file selection, chunking and analyzer

**Files:**
- Create: `src/scope_agent/domain/file_selection.py`, `src/scope_agent/domain/chunking.py`, `src/scope_agent/services/code_analysis.py`
- Test: `tests/domain/test_file_selection.py`, `test_chunking.py`, `tests/services/test_code_analysis.py`

**Interfaces:**
- Produces: `select_files(files, exclude: list[str], priority: list[str]) -> list[FileChange]`. It drops excluded paths and binary or oversize files, puts priority matches first, and orders the rest by diff size ascending. `unified_diff(fc) -> str` uses 3 lines of context.
- Produces: `chunk(diffs: list[tuple[str, str]], max_tokens: int, max_chunks: int) -> tuple[list[list[tuple[str, str]]], list[str]]`, returning the chunks and the skipped paths.
  - Tokens are estimated as `len // 4`.
  - 25% of the budget is reserved for ticket context.
- Produces: `CodeAnalyzer(llm, exclude, priority, max_input_tokens, max_chunks)` with `analyze(commit, tickets: list[TicketContext]) -> CodeAnalysis(findings: list[Finding], ac_quality: Literal["ok","vague","missing"] | None, errors: list[str])`.
  - `ac_quality` is the worst value across chunks, or `None` when there are no tickets.
  - Skipped paths produce an `info` `risky_change` finding with category `partial_analysis`, listing the paths as evidence.
  - With no tickets, it drops `out_of_scope` findings.

- [ ] **Step 1: Write failing tests**
  - `test_select_excludes_lock_and_prioritises_application_yml`
  - `test_chunk_budget_and_skipped`: 5 × 40 000 chars with `max_tokens=60_000` and `max_chunks=3` gives at most 3 chunks and a non-empty skipped list.
  - `test_partial_analysis_finding`
  - `test_no_ticket_drops_out_of_scope`
  - `test_worst_ac_quality`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: file selection, chunking and code analysis service`

---

### Task 12: Ticket gaps and push pipeline

**Files:**
- Create: `src/scope_agent/domain/ticket_gaps.py`, `src/scope_agent/services/pipeline.py`
- Test: `tests/domain/test_ticket_gaps.py`, `tests/services/test_pipeline.py`

**Interfaces:**
- Produces: `ticket_gaps(commit: CommitData, keys: list[str], tickets: dict[str, TicketContext | None], ac_quality: str | None, already_reported: set[tuple[str, str]]) -> list[Finding]`. It is pure, and it updates `already_reported` so each `(ticket, category)` pair is reported once per push.

  | condition | category | severity |
  |---|---|---|
  | no keys | `missing_key` | `low` |
  | ticket is None | `ticket_not_found` | `info` |
  | `acceptance_criteria is None` | `missing_ac` | `info` |
  | `ac_quality == "vague"` | `vague_ac` | `info` |
  | `epic_key is None` | `no_epic` | `info` |

- Produces: `PushResult(job_id: int, job: PushJob, commits: list[CommitData], findings: list[Finding], summaries: dict[str, list[LibrarySummary]])`
- Produces: `PushPipeline(bitbucket, tickets: TicketResolver, deps: DependencyAnalyzer, code: CodeAnalyzer, db: Database, findings: FindingRepository, ledger: CommitLedger, project_keys: list[str], max_commits: int)` with `run(job_id: int, job: PushJob, force: bool = False) -> PushResult`. The steps:
  1. Get the SHAs. Drop those already analyzed (unless `force`) and merges; merges are still marked as analyzed.
  2. For each commit, `get_commit`, `extract_keys`, then resolve each key once per push.
  3. Build `dependency_changes` for every file, then call `deps.analyze` once for the whole push.
  4. Call `code.analyze` for each commit, then `ticket_gaps`.
  5. Fill in `repo`, `commit`, `author_email` and `jira_keys` on each finding.
  6. In **one** `db.session()`: `findings.save(...)` and `ledger.mark_analyzed(...)`.
  7. `BudgetExceeded` propagates, and nothing is persisted.

- [ ] **Step 1: Write failing tests**
  - `test_gap_rules_table`: one case per row.
  - `test_gap_reported_once_per_ticket_per_push`
  - `test_pipeline_skips_analyzed_and_merges` *(Review Focus 1)*
  - `test_pipeline_force_reanalyzes`
  - `test_pipeline_missing_key_still_runs_code_analysis`
  - `test_pipeline_dependency_files_dispatched_and_cross_commit_swap`
  - `test_pipeline_enriches_and_persists_atomically`
  - `test_pipeline_budget_exceeded_persists_nothing`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: ticket gap rules and push analysis pipeline`

---

### Task 13: Recipient routing rules

**Files:**
- Modify: `src/scope_agent/domain/routing.py` (add functions)
- Test: `tests/domain/test_routing.py`

**Interfaces:**
- Produces: `reviewers_for(finding: Finding, routing: RoutingConfig) -> list[str]`. It collects:
  - the repo leads (the key may be the slug or `"PROJ/slug"`);
  - the path owners whose globs match `finding.files` (`fnmatch`);
  - `categories[finding.category]`.

  It falls back to `default_leads` when there are no repo or path leads. Results are lower-cased, deduped and kept in order.
- Produces: `push_recipients(findings: list[Finding], commits: list[CommitData], routing) -> list[str]`: the authors of the flagged commits, plus the union of `reviewers_for`.

- [ ] **Step 1: Write failing tests**
  - `test_repo_path_category_owners`: a `connection_model` finding on `src/main/java/x/flags/F.java` gives `["lead-a@corp","flags-owner@corp","gateway-owner@corp","platform-sre@corp"]`.
  - `test_unknown_repo_default_leads`
  - `test_missing_author_still_leads`
  - `test_slug_or_project_slug_key`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: recipient routing rules`

---

### Task 14: Notifications: rating links, email rendering, SMTP, push notifier and digest service

**Files:**
- Create: `src/scope_agent/notify/{__init__,rating_links,push_email,digest_email}.py`, `src/scope_agent/notify/templates/{push.html.j2,push.txt.j2,digest.html.j2,digest.txt.j2}`, `src/scope_agent/adapters/smtp.py`, `src/scope_agent/services/push_notifier.py`, `src/scope_agent/services/digest.py`
- Test: `tests/notify/test_rating_links.py`, `test_push_email.py`, `test_digest_email.py`, `tests/adapters/test_smtp.py`, `tests/services/test_push_notifier.py`, `test_digest.py`

**Interfaces:**
- Produces: `rating_token(secret, finding_id, value) -> str` (the first 32 hex characters of HMAC-SHA256) and `rating_url(base, secret, finding_id, value) -> str`, giving `{base}/rate/{id}?v={value}&t={token}`.
- Produces: `EmailSender` protocol, `send(msg: EmailMessage, to: list[str]) -> None`. `SmtpSender(host, port, user, password, starttls, sender)` implements it. With no recipients it logs and returns.
- Produces: `LinkBuilder(bitbucket_url, jira_url, public_base_url, rating_secret)` with `commit(repo, sha)`, `ticket(key)` and `rating(finding_id, value)`.
- Produces: `render_push(result: PushResult, links: LinkBuilder) -> EmailMessage`.
  - Subject: `[Scope Agent] {n} item(s) for review: {repo} {branch} ({first7}..{last7})`.
  - Body sections, in order:
    1. findings with severity `low` or above, high first. Each shows the title, why it was flagged, evidence, links, suggested reviewers, and the useful and noise links.
    2. a "Library changes" section.
    3. the footer: "This is an automated review aid. Nothing has been blocked."
- Produces: `render_digest(findings, errors: int, deferred: int, start, end, links) -> EmailMessage`.
  - Subject: `[Scope Agent] Daily digest {YYYY-MM-DD}: {high} high, {total} findings`.
  - Body: findings grouped repo → ticket (or "No ticket"), high first, all severities.
  - Gap statistics: commits without a key, tickets with missing ACs, and tickets with vague ACs.
  - The lines `Analysis errors: {n}` and `Deferred (budget): {n}`.
- Produces: `should_send_push(result) -> bool`. It is true if any finding is `low` or above, or if any `LibrarySummary` exists.
- Produces: `PushNotifier(routing: RoutingConfig, sender: EmailSender, links: LinkBuilder)` with `notify(result: PushResult) -> None`. It fills in `suggested_reviewers`, checks `should_send_push`, renders, and sends to `push_recipients`.
- Produces: `DigestService(findings: FindingRepository, jobs: JobQueue, meta: MetaStore, sender, links, recipients: list[str], clock)` with `run() -> None`. The window runs from `meta["last_digest_at"]`, or from now minus 24 h, until now. It sends, then sets `last_digest_at`.

- [ ] **Step 1: Write failing tests**
  - `test_rating_token_stable`
  - `test_should_send_push_rules`: info-only with no summaries gives False; a summary gives True.
  - `test_push_subject_order_links`: high appears before low, the body contains `/rate/{id}?v=useful&t=` and `/browse/PAY-12`, and no info findings appear.
  - `test_digest_subject_and_counts`: `2 high, 5 findings`, `Analysis errors: 1` and `Deferred (budget): 2`.
  - `test_smtp_sends_starttls`: patch `smtplib.SMTP`.
  - `test_notifier_routes_to_gateway_owner_for_connection_model`
  - `test_digest_window_from_meta_and_updates_meta`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use multipart text and HTML via Jinja2 `PackageLoader("scope_agent.notify","templates")`.
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: rating links, push/digest emails, smtp sender, notifier and digest service`

---

### Task 15: Web process and worker process

**Files:**
- Create: `src/scope_agent/web/{__init__,app,webhook,rating}.py`, `src/scope_agent/worker/{__init__,retry_policy,worker}.py`
- Test: `tests/web/test_webhook.py`, `test_rating.py`, `tests/worker/test_retry_policy.py`, `test_worker.py`

**Interfaces:**
- Produces: `create_app(webhook_secret: str, branches: list[str], jobs: JobQueue, ratings: RatingRepository, rating_secret: str) -> FastAPI`, which mounts these routers:
  - **`POST /webhook/bitbucket`**:
    - reads the raw body first;
    - `X-Event-Key: diagnostics:ping` → 200;
    - a bad signature → 401;
    - `repo:refs_changed` → enqueue the jobs → 202 `{"queued": n}`;
    - any other event → 204.
  - **`GET /rate/{finding_id}?v=&t=`**: a valid token saves the rating and returns 200 HTML "Thanks, recorded."; an invalid one returns 403.
  - **`GET /healthz`** → 200.
- Produces: `RetryPolicy(backoff_seconds: list[int])` with `next_attempt_at(attempts: int, now) -> datetime | None`. It returns `None` once attempts exceed the list length.
- Produces: `Worker(jobs: JobQueue, pipeline: PushPipeline, notifier: PushNotifier, errors: FindingRepository, retry: RetryPolicy, lease_seconds: int, timezone: str, clock)`:
  - `run_once() -> bool`:
    - claim a job, run the pipeline, notify, then `complete`;
    - on `BudgetExceeded`, `defer` until local midnight plus 5 minutes, in `timezone`;
    - on any other exception, `retry` per the policy, or `fail` and record the error with `errors.save(job_id, [], {}, [(repo, None, message)])`.
  - `run(stop: threading.Event, poll_seconds: float)`: loops on `run_once` until stopped. SIGTERM sets `stop`. A job that is mid-run when `stop` is set may finish. If the process is killed, the lease handles recovery.
  - A notifier exception after the pipeline has succeeded is logged and the job is still completed. Findings are already saved, so a failed email must not cause a re-analysis.

- [ ] **Step 1: Write failing tests**
  - `test_webhook_bad_signature_401`
  - `test_webhook_ping_200`
  - `test_webhook_enqueue_and_redelivery_dedup`: the same body twice gives `queued` 2 and then 0 *(Review Focus 1)*.
  - `test_rate_valid_and_invalid`
  - `test_retry_policy_schedule_and_exhaustion`
  - `test_worker_happy_path_completes`
  - `test_worker_defers_on_budget`
  - `test_worker_retries_then_fails_records_error` *(Review Focus 3)*
  - `test_worker_survives_exception_and_processes_next`
  - `test_worker_email_failure_still_completes`
  - `test_worker_stop_event_exits_loop`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** `feat: web process (webhook, ratings) and leased worker process`

---

### Task 16: Composition root, CLI process types, container and incident replay

**Files:**
- Create: `src/scope_agent/composition.py`, `src/scope_agent/cli.py`, `Dockerfile`, `docker-compose.yml`, `README.md`
- Test: `tests/test_composition.py`, `tests/test_cli.py`, `tests/test_incident_replay.py`

**Interfaces:**
- Produces: `Container` (a dataclass) and `build_container(settings: Settings) -> Container`. This is the **only** place concrete adapters, repositories and services are constructed. It loads the routing and hints files via `config_files`, and logs startup config with secrets masked.
- Produces: a typer `app`. Each command calls `load_settings()`, then `configure_logging`, then `build_container`:
  - `web`: uvicorn `create_app(...)` on `0.0.0.0:settings.port`.
  - `worker`: `Worker.run`, with SIGTERM/SIGINT handlers that set the stop event.
  - `digest`: `DigestService.run()` once, then exit. The platform scheduler runs this.
  - `migrate`: `Database.create_all()`.
  - `analyze REPO SHA [--send-email/--no-send-email=no] [--json]`: a synthetic `PushJob(from_hash=parent, to_hash=SHA)`, run through `pipeline.run(force=True)`. Output is a table or JSON on stdout.
  - `backtest REPO --branch development --limit 200 [--send-email/--no-send-email=yes]`
  - `report`: the useful rate per type × severity, plus overall high+medium against 70%.
- **`Dockerfile`:**
  - Python 3.12-slim, multi-stage `uv sync --frozen --no-dev`;
  - non-root user, `ENTRYPOINT ["scope-agent"]`, `CMD ["web"]`;
  - no config or data files copied in.
- **`docker-compose.yml`:**
  - services `postgres:16`, `mailpit`, `migrate` (one-off), `web` (port `${PORT:-8080}`) and `worker`;
  - all of them use `env_file: .env`;
  - `./deploy` is mounted read-only at `/config`, and the `.env.example` comments point `SCOPE_ROUTING_FILE` and `SCOPE_DEPENDENCY_HINTS_FILE` there.
- **`README.md`:**
  - the twelve-factor process model;
  - a table of every env var (generated from `.env.example`);
  - how to mount the data files;
  - creating the Bitbucket DC webhook (Repository → Push, the secret, `{host}/webhook/bitbucket`);
  - a JIRA Cloud API token and the Nexus read-only account;
  - scheduling `digest`, for example `0 18 * * 1-5` as a K8s CronJob or cron entry;
  - **manual incident replay:** add the flagd docs URL to the dependency hints, then run `scope-agent analyze PAY/<repo> <swap-sha> --json`. Expect a high `connection_model` finding.

- [ ] **Step 1: Write failing tests**
  - `test_build_container_from_env`: uses the full env from Task 1 with `sqlite://`; all fields are non-None, and no network is used at build time.
  - `test_no_module_reads_os_environ`: greps `src/` for `os.environ` or `os.getenv` and finds nothing outside `settings.py`.
  - `test_only_composition_constructs_adapters`: AST-scans `src/` for calls to `BitbucketClient(`, `JiraClient(`, `NexusClient(`, `GitHubClient(`, `SmtpSender(` and `AnthropicClient(`. They are allowed only in `composition.py` and `llm/factory.py`.
  - `test_cli_analyze_json`: monkeypatches `build_container` with fakes.
  - `test_cli_report_useful_rate`
  - `test_cli_exits_2_on_missing_env`
  - `test_incident_replay_wiring` (fakes, no network):
    - **Input:** a `pom.xml` swap from `com.corp:flag-util:1.3.0` to `dev.openfeature.contrib.providers:flagd:0.8.1`, plus the `Flags.java` import change. The fake LLM returns a high `connection_model` finding for the swap prompt.
    - **Assertions:** one push email; recipients include `gateway-owner@corp`; the body contains the finding title and a "Library changes" section for flagd.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -v`, then `docker compose build && docker compose run --rm migrate && docker compose up -d web worker && curl -fsS localhost:8080/healthz`. Expected: all tests PASS, and the health check returns 200.
- [ ] **Step 5: Commit** `feat: composition root, cli process types, container setup and incident replay test`
