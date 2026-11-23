# Commit Scope & Risk Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A webhook service that analyzes every commit pushed to `development`, `hotfix/*` and `release/*` on Bitbucket Data Center. It flags risky runtime changes (especially dependency upgrades and swaps), out-of-scope work and ticket gaps against JIRA Cloud. It emails the author and leads per push and sends PMO a daily digest. It is report-only.

**Architecture:** A FastAPI receiver verifies and enqueues `repo:refs_changed` events into a DB job table. A background worker runs the analysis pipeline:
- Bitbucket, to fetch commits and before/after file contents
- JIRA, for ticket, ACs and Epic
- the dependency extractors (Maven/npm), plus Nexus, GitHub and Bitbucket for release notes and docs
- the LLM, behind a swappable interface

Findings are persisted, and push emails are sent. An APScheduler job sends the PMO digest. A CLI runs one-off analysis, backtests and the rating report.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, httpx, SQLAlchemy 2 (SQLite for the POC), pydantic v2 + pydantic-settings, PyYAML, Jinja2, APScheduler 3, anthropic SDK, typer. Tests: pytest, respx.

**Spec:** `docs/superpowers/specs/2026-11-23-commit-scope-risk-agent-design.md`

## Global Constraints

- **Report-only:** nothing blocks a push or a merge, and the agent never writes to Bitbucket or JIRA.
- **Branches analyzed:** `development`, `hotfix/*`, `release/*` (configurable globs). All other branches are ignored.
- **Bitbucket Data Center:** REST API `1.0`, Bearer HTTP access token. The webhook signature is the `X-Hub-Signature: sha256=<hex>` HMAC of the raw body.
- **JIRA Cloud:** REST API v3, basic auth with service-account email + API token. Epic comes from the `parent` field. Rich text is ADF.
- **Dependencies:** resolved through internal **Nexus**, read-only. Ecosystems are Maven and npm only.
- **LLM access:** only through `LLMClient` (Task 9). No other module imports `anthropic`.
- **Severity order:** `high` > `medium` > `low` > `info`. `info` findings are digest-only.
- **Unknown is not safe:** missing release notes for a version jump produce a finding. They never produce silence.
- **Tone:** email copy is neutral and "for review", and every finding states why it was flagged.
- **Budget:** a daily LLM token budget. When it's exhausted, the job is **deferred to the next day** and counted in the digest, never dropped.
- **Secrets** come from env vars only. Behavior comes from `config.yaml` + `routing.yaml`.

## Review Focus

1. **Duplicate deliveries:** Bitbucket redelivers the webhook, or the same commit lands on `development` and then `release/x`. Each commit is analyzed once, and at most one email goes out per commit. *Owned by Tasks 2 and 12.*
2. **LLM returns invalid JSON or the API errors:** retry once, then record an analysis error. The worker survives, and the error count appears in the digest. *Owned by Tasks 9 and 12.*
3. **Release notes unavailable** (GitHub 404 or rate-limit, no CHANGELOG): this yields a `behavior_unknown` finding (medium for a major bump, low otherwise), never an empty result. *Owned by Tasks 8 and 10.*
4. **No routing entry for the repo, or the author email is missing:** fall back to `default_leads`. The email is still sent to whoever is known. *Owned by Task 13.*
5. **Huge diff or lockfile:** stays within `max_input_tokens_per_call`. Highest-priority files go first, and a `partial_analysis` info finding is recorded. *Owned by Task 11.*

---

## File Structure

```
pyproject.toml, .gitignore, config.example.yaml, routing.example.yaml, README.md
src/scope_agent/
  config.py            Settings (env) + AppConfig/RoutingConfig (YAML)
  models.py            shared pydantic models (Finding, CommitData, TicketContext, DependencyChange, ...)
  db.py                SQLAlchemy tables + Store (jobs, dedup, findings, summaries, ratings, caches, usage)
  versions.py          version parsing / comparison / "between" / major-bump
  bitbucket/webhook.py signature check, payload → PushJob list, branch filter
  bitbucket/client.py  BitbucketClient (commits, changes, raw files, tags, file listing)
  jira/keys.py         JIRA key extraction
  jira/client.py       JiraClient + ADF → text + AC extraction
  deps/maven.py        pom.xml before/after → DependencyChange list
  deps/npm.py          package.json / package-lock.json before/after → DependencyChange list
  deps/swaps.py        swap detection
  deps/sources.py      Nexus lookup → source repo; ReleaseNotesFetcher (GitHub / CHANGELOG / Bitbucket / overrides)
  llm/client.py        LLMClient protocol, ClaudeClient, BudgetGuard, response schemas
  llm/prompts/*.j2     dependency_upgrade, dependency_new, dependency_swap, code_review
  analysis/dependency.py  dependency findings + reviewer summaries
  analysis/code.py        file selection/prioritisation/chunking + code/scope findings
  analysis/pipeline.py    analyze_commit / analyze_push orchestration, ticket gaps
  notify/routing.py    recipients resolution
  notify/email.py      push email, digest, SMTP sender, rating links
  notify/templates/*.j2
  web.py               FastAPI app (webhook, rating endpoint, health)
  worker.py            job loop
  scheduler.py         digest schedule
  cli.py               typer CLI: serve, analyze, backtest, digest, report
tests/ (mirrors src; fixtures in tests/fixtures/)
```

---

### Task 1: Project scaffold, config and shared models

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `config.example.yaml`, `routing.example.yaml`, `src/scope_agent/__init__.py`, `src/scope_agent/config.py`, `src/scope_agent/models.py`
- Test: `tests/test_config.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `Settings` (pydantic-settings, prefix `SCOPE_`) with these fields:
    - `bitbucket_url`, `bitbucket_token`, `webhook_secret`
    - `jira_url`, `jira_email`, `jira_token`
    - `anthropic_api_key`, `github_token: str | None`
    - `nexus_url`, `nexus_user`, `nexus_password`, `nexus_maven_repo="maven-public"`, `nexus_npm_repo="npm-group"`
    - `smtp_host`, `smtp_port=587`, `smtp_user: str | None`, `smtp_password: str | None`, `smtp_from`
    - `db_url="sqlite:///scope_agent.db"`, `config_path="config.yaml"`
    - `public_base_url`, `rating_secret`
  - `AppConfig` with these fields:
    - `branches: list[str]`
    - `jira: JiraConfig(project_keys: list[str], ac_field: str | None, ac_heading="Acceptance Criteria")`
    - `llm: LLMConfig(model="claude-sonnet-5-5", daily_token_budget=2_000_000, max_input_tokens_per_call=60_000)`
    - `files: FilesConfig(exclude: list[str], priority: list[str])`
    - `max_commits_per_push=200`
    - `digest: DigestConfig(time="18:00", timezone="UTC", recipients: list[str])`
    - `library_docs: dict[str, list[str]]`, `internal_repos: dict[str, str]` (library → `"PROJ/slug"`), `known_swaps: list[tuple[str, str]]`
    - `routing_file="routing.yaml"`
  - `load_app_config(path) -> AppConfig`
  - `RoutingConfig(repos: dict[str, RepoRouting(leads: list[str], paths: dict[str, list[str]])], categories: dict[str, list[str]], default_leads: list[str])`
  - `load_routing(path) -> RoutingConfig`
  - Models in `models.py`:
    - `Severity` (str enum `high|medium|low|info`) with `rank` property (high=3 … info=0)
    - `FindingType` (`dependency_behavior_change|risky_change|out_of_scope|ticket_gap`)
    - `Finding`: `id: str` (uuid4 default), `repo` (`"PROJ/slug"`), `commit`, `author_email: str | None`, `jira_keys: list[str]`, `type`, `category: str`, `severity`, `title`, `explanation`, `evidence: list[str]`, `files: list[str]`, `suggested_reviewers: list[str]`, `created_at`
    - `FileChange(path, status: Literal["added","modified","deleted"], before: str | None, after: str | None)`
    - `CommitData(repo, sha, author_name, author_email: str | None, message, parents: list[str], files: list[FileChange])` with an `is_merge` property
    - `TicketContext(key, summary, description, issue_type, status, acceptance_criteria: str | None, components: list[str], labels: list[str], epic_key: str | None, epic_summary: str | None, epic_description: str | None, linked_titles: list[str])`
    - `DependencyChange(ecosystem: Literal["maven","npm"], name, old_version: str | None, new_version: str | None, kind: Literal["added","removed","upgraded","downgraded","parent","bom","transitive"], file: str)`
    - `LibrarySummary(library, kind, summary, links: list[str])`
    - `PushJob(repo, branch, from_hash: str | None, to_hash, pushed_by: str | None)`

- [ ] **Step 1: Write failing tests**

```python
def test_load_app_config_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("branches: [development, 'hotfix/*', 'release/*']\njira: {project_keys: [PROJ]}\ndigest: {recipients: [pmo@corp]}\n")
    cfg = load_app_config(p)
    assert cfg.branches == ["development", "hotfix/*", "release/*"]
    assert cfg.llm.daily_token_budget == 2_000_000
    assert cfg.jira.ac_heading == "Acceptance Criteria"
    assert cfg.max_commits_per_push == 200

def test_load_routing(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("repos: {payments-service: {leads: [a@corp], paths: {'src/**/flags/**': [f@corp]}}}\ncategories: {connection_model: [gw@corp]}\ndefault_leads: [em@corp]\n")
    r = load_routing(p)
    assert r.repos["payments-service"].paths["src/**/flags/**"] == ["f@corp"]
    assert r.categories["connection_model"] == ["gw@corp"]

def test_severity_rank_order():
    assert Severity.high.rank > Severity.medium.rank > Severity.low.rank > Severity.info.rank

def test_commit_is_merge():
    c = CommitData(repo="P/r", sha="a", author_name="x", author_email=None, message="m", parents=["1", "2"], files=[])
    assert c.is_merge
```

- [ ] **Step 2: Run** `pytest tests/test_config.py tests/test_models.py -v`. Expected: FAIL (import errors).
- [ ] **Step 3: Implement** `config.py` and `models.py` per the Interfaces above. Add `pyproject.toml` with a `src` layout, the dependencies from the Tech Stack, a `scope-agent = "scope_agent.cli:app"` script, and pytest `testpaths=["tests"]`. The example YAMLs mirror the spec §6.7 routing example and the `files.exclude`/`files.priority` globs from spec §6.5:
  - `exclude`: `**/package-lock.json`, `**/*.min.js`, `**/generated/**`, `**/dist/**`, `**/target/**`, `**/node_modules/**`
  - `priority`: `**/application*.yml`, `**/application*.yaml`, `**/application*.properties`, `**/*envoy*.y*ml`, `**/*gateway*.y*ml`, `**/helm/**`, `**/k8s/**`, `**/Dockerfile`, `**/environment*.ts`, `**/proxy.conf.json`
- [ ] **Step 4: Run** `pip install -e '.[dev]' && pytest -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: project scaffold, config and shared models`

---

### Task 2: Persistence (Store)

**Files:**
- Create: `src/scope_agent/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `Finding`, `LibrarySummary`, `PushJob` (Task 1)
- Produces: `Store(db_url: str, clock: Callable[[], datetime] = datetime.utcnow)` with `create_all()` and these methods:
  - **Jobs:**
    - `enqueue(job: PushJob, dedup_key: str) -> int | None` returns `None` if `dedup_key` already exists. The key is `f"{repo}:{branch}:{to_hash}"`.
    - `claim_next(now: datetime) -> tuple[int, PushJob] | None`: oldest job where `status in (queued, deferred)` and `not_before <= now`. It sets `running` and increments `attempts`.
    - `complete(job_id)`, `fail(job_id, error: str, retry_at: datetime | None)` (status `failed` when `retry_at` is None, otherwise `queued`), `defer(job_id, until: datetime)`
  - **Commit dedup:** `is_analyzed(repo, sha) -> bool`, `mark_analyzed(repo, sha)`
  - **Results:**
    - `save_findings(push_job_id: int, findings: list[Finding])`, `save_summaries(push_job_id: int, commit: str, summaries: list[LibrarySummary])`
    - `findings_since(since: datetime) -> list[Finding]`, `get_finding(finding_id) -> Finding | None`
    - `record_error(repo, commit: str | None, error: str)`, `errors_since(since) -> int`, `deferred_count() -> int`
  - **Ratings:** `save_rating(finding_id, value: Literal["useful","noise"])` (upsert per finding), `rating_report() -> list[dict]` with rows `{type, severity, useful, noise}`
  - **Caches:** `cache_get(namespace, key) -> str | None`, `cache_put(namespace, key, value, ttl_seconds: int | None)` (None means permanent)
  - **Usage:** `add_tokens(day: date, tokens: int)`, `tokens_used(day: date) -> int`
  - **Digest bookkeeping:** `get_meta(key) -> str | None`, `set_meta(key, value)`

- [ ] **Step 1: Write failing tests** (use `Store("sqlite://")`):

```python
def test_enqueue_dedups(store, job):
    assert store.enqueue(job, "P/r:development:abc") is not None
    assert store.enqueue(job, "P/r:development:abc") is None

def test_claim_respects_not_before_and_defer(store, job):
    jid = store.enqueue(job, "k1")
    store.defer(jid, until=datetime(2030, 1, 1))
    assert store.claim_next(datetime(2029, 12, 31)) is None
    claimed = store.claim_next(datetime(2030, 1, 2))
    assert claimed[0] == jid

def test_fail_with_retry_requeues(store, job):
    jid = store.enqueue(job, "k2"); store.claim_next(datetime.now())
    store.fail(jid, "boom", retry_at=datetime(2000, 1, 1))
    assert store.claim_next(datetime.now())[0] == jid

def test_commit_dedup(store):
    assert not store.is_analyzed("P/r", "abc"); store.mark_analyzed("P/r", "abc")
    assert store.is_analyzed("P/r", "abc")

def test_findings_roundtrip_and_rating_report(store, finding):
    store.save_findings(1, [finding])
    assert store.get_finding(finding.id).title == finding.title
    store.save_rating(finding.id, "useful"); store.save_rating(finding.id, "noise")  # upsert → noise
    assert store.rating_report() == [{"type": finding.type, "severity": finding.severity, "useful": 0, "noise": 1}]

def test_cache_ttl():
    now = [datetime(2026, 1, 1)]; s = Store("sqlite://", clock=lambda: now[0]); s.create_all()
    s.cache_put("jira", "PAY-1", "{}", ttl_seconds=1); assert s.cache_get("jira", "PAY-1") == "{}"
    now[0] += timedelta(seconds=2); assert s.cache_get("jira", "PAY-1") is None
def test_tokens_used_per_day(store):
    store.add_tokens(date(2026, 11, 23), 100); store.add_tokens(date(2026, 11, 23), 50)
    assert store.tokens_used(date(2026, 11, 23)) == 150
```

- [ ] **Step 2: Run** `pytest tests/test_db.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `db.py` with SQLAlchemy Core or ORM, using tables `jobs`, `analyzed_commits` (PK repo+sha), `findings` (lists stored as JSON), `library_summaries`, `ratings`, `errors`, `cache`, `token_usage`, `meta`. Inject a `clock: Callable[[], datetime]` (default `datetime.utcnow`) for the TTL test.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: persistence store for jobs, findings, caches and ratings`

---

### Task 3: Webhook verification and parsing

**Files:**
- Create: `src/scope_agent/bitbucket/__init__.py`, `src/scope_agent/bitbucket/webhook.py`, `tests/fixtures/refs_changed.json`
- Test: `tests/bitbucket/test_webhook.py`

**Interfaces:**
- Consumes: `PushJob`
- Produces:
  - `verify_signature(secret: str, body: bytes, header: str | None) -> bool`, using constant-time compare against `"sha256=" + hmac_sha256_hex`
  - `parse_refs_changed(payload: dict, branch_globs: list[str]) -> list[PushJob]`

The fixture is a DC `repo:refs_changed` payload with:
- `repository.slug = "payments-service"` and `repository.project.key = "PAY"`
- `actor.emailAddress`
- four `changes`:
  1. `UPDATE` on `refs/heads/development` (`fromHash` `aaa…`, `toHash` `bbb…`)
  2. `ADD` on `refs/heads/release/1.4` (`fromHash` `0000000000000000000000000000000000000000`)
  3. `DELETE` on `refs/heads/hotfix/x`
  4. `UPDATE` on `refs/heads/feature/PAY-1`
- one tag change: `ref.type = "TAG"`

- [ ] **Step 1: Write failing tests**

```python
def test_verify_signature_ok_and_bad():
    body = b'{"x":1}'; sig = "sha256=" + hmac.new(b"s3", body, hashlib.sha256).hexdigest()
    assert verify_signature("s3", body, sig)
    assert not verify_signature("s3", body, "sha256=deadbeef")
    assert not verify_signature("s3", body, None)

def test_parse_filters_branches_tags_and_deletes():
    jobs = parse_refs_changed(load_fixture("refs_changed.json"), ["development", "hotfix/*", "release/*"])
    assert [(j.repo, j.branch) for j in jobs] == [("PAY/payments-service", "development"), ("PAY/payments-service", "release/1.4")]
    assert jobs[1].from_hash is None   # all-zero hash → new branch
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Match branch globs with `fnmatch.fnmatchcase` on `ref.displayId`. Skip changes where `type == "DELETE"` or `ref.type != "BRANCH"`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: bitbucket webhook signature check and payload parsing`

---

### Task 4: Bitbucket DC client

**Files:**
- Create: `src/scope_agent/bitbucket/client.py`
- Test: `tests/bitbucket/test_client.py` (respx)

**Interfaces:**
- Consumes: `Settings`, `CommitData`, `FileChange`, `PushJob`
- Produces: `BitbucketClient(base_url, token, http: httpx.Client | None = None)` with:
  - `commits_in_push(job: PushJob, limit: int) -> list[str]`: SHAs oldest-first. It calls `GET /rest/api/1.0/projects/{p}/repos/{r}/commits?until={to}&since={from}`. If `from_hash` is None, `since` is `latestCommit` from `GET .../branches/default`. It follows `isLastPage`/`nextPageStart`. If there are more than `limit` commits, it returns the newest `limit`.
  - `get_commit(repo: str, sha: str) -> CommitData`:
    - `GET .../commits/{sha}` gives author, message and parents.
    - `GET .../commits/{sha}/changes` (paged) gives paths and types: `ADD|MODIFY|DELETE|MOVE|COPY`. `MOVE`/`COPY` map to `modified`, using the new path.
    - Content comes from `GET .../raw/{path}?at={sha}` for `after` and `?at={parents[0]}` for `before`. 404 means None. Skip binary content (a NUL byte in the first 8 KB) and files over 1 MB, keeping a `FileChange` with `before=after=None`.
  - `get_file(repo, path, at) -> str | None`
  - `list_files(repo, at, globs: list[str], limit=50) -> list[str]` via `GET .../files?at=` (paged), filtered by glob
  - `list_tags(repo) -> list[str]`
  - `commit_messages_between(repo, since_ref, until_ref, limit=200) -> list[str]`
  - `repo` is always `"PROJ/slug"`.

- [ ] **Step 1: Write failing tests**
  - `test_commits_in_push_paginates_oldest_first`: two pages returning newest-first → `["c1","c2","c3"]`.
  - `test_commits_in_push_new_branch_uses_default_branch`: `from_hash=None` → asserts the request had `since=<default head>`.
  - `test_get_commit_builds_file_changes`: one ADD, one MODIFY, one DELETE; the raw 404 for ADD-before → `before is None`. Asserts the author email and parents.
  - `test_get_commit_skips_binary`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** with httpx, `Authorization: Bearer`, timeout 30 s, and a retry on 5xx/429 (3 tries, exponential backoff).
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: bitbucket data center REST client`

---

### Task 5: JIRA key extraction and JIRA Cloud client

**Files:**
- Create: `src/scope_agent/jira/__init__.py`, `src/scope_agent/jira/keys.py`, `src/scope_agent/jira/client.py`, `tests/fixtures/jira_issue.json`, `tests/fixtures/jira_epic.json`
- Test: `tests/jira/test_keys.py`, `tests/jira/test_client.py`

**Interfaces:**
- Consumes: `TicketContext`, `Store` (cache)
- Produces:
  - `extract_keys(message: str, branch: str | None, project_keys: list[str]) -> list[str]`: ordered and unique. It applies regex `\b([A-Z][A-Z0-9]+-\d+)\b` to the message and keeps only configured project keys. The branch is a fallback, used only when the message has none.
  - `adf_to_text(doc: dict | None) -> str`
  - `extract_ac(description_text: str, heading: str) -> str | None`
  - `JiraClient(base_url, email, token, ac_field: str | None, ac_heading: str, store: Store, http=None)` with `get_ticket(key) -> TicketContext`. It raises `TicketNotFound(key)` on 404 or 403. Cache in namespace `jira` with a TTL of 600 s.

- [ ] **Step 1: Write failing tests**

```python
def test_extract_keys_conventional_commit():
    assert extract_keys("feat(PAY-12): add flag client\n\nRefs: PAY-13", None, ["PAY"]) == ["PAY-12", "PAY-13"]
def test_extract_keys_ignores_unknown_projects():
    assert extract_keys("fix: UTF-8 handling", None, ["PAY"]) == []
def test_extract_keys_branch_fallback():
    assert extract_keys("fix: typo", "feature/PAY-99-typo", ["PAY"]) == ["PAY-99"]
def test_adf_to_text_lists_and_headings():
    assert adf_to_text(ADF_FIXTURE) == "## Acceptance Criteria\n- flags load on startup\n- falls back to defaults"
def test_extract_ac_from_heading():
    assert extract_ac("Intro\n## Acceptance Criteria\n- a\n- b\n## Notes\nx", "Acceptance Criteria") == "- a\n- b"
def test_extract_ac_plain_label_to_end():
    assert extract_ac("Intro\nAcceptance criteria:\n- a", "Acceptance Criteria") == "- a"
```

`adf_to_text` renders heading nodes as `## text`. `extract_ac` finds the first line matching `^#*\s*{heading}\s*:?$` (case-insensitive) and returns the trimmed lines up to the next line starting with `#`, or to the end.

  - `test_get_ticket_with_epic_parent`: the issue's `parent.fields.issuetype.name == "Epic"` → `epic_key`, and the epic summary and description are fetched.
  - `test_get_ticket_subtask_walks_to_epic`: the parent is a Story whose parent is the Epic.
  - `test_get_ticket_uses_ac_field_when_configured`: `ac_field="customfield_10035"`.
  - `test_get_ticket_404_raises`
  - `test_get_ticket_cached`: the second call makes no HTTP request.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Use `GET /rest/api/3/issue/{key}?fields=summary,description,issuetype,status,components,labels,parent,issuelinks[,{ac_field}]` with basic auth `(email, token)`. Serialize `TicketContext` to JSON for the cache.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: jira key extraction and jira cloud client`

---

### Task 6: Version utilities and dependency change extraction (Maven + npm)

**Files:**
- Create: `src/scope_agent/versions.py`, `src/scope_agent/deps/__init__.py`, `src/scope_agent/deps/maven.py`, `src/scope_agent/deps/npm.py`, plus fixtures `tests/fixtures/pom_before.xml`, `pom_after.xml`, `package_before.json`, `package_after.json`, `lock_before.json`, `lock_after.json`
- Test: `tests/test_versions.py`, `tests/deps/test_maven.py`, `tests/deps/test_npm.py`

**Interfaces:**
- Consumes: `DependencyChange`
- Produces:
  - `version_key(v: str) -> tuple`: numeric parts compare numerically. A pre-release qualifier (`-rc1`, `-SNAPSHOT`, `-beta`) sorts before the release.
  - `is_major_bump(old, new) -> bool`: the first numeric component differs, or for `0.x` the second does.
  - `versions_between(all_versions: list[str], old: str, new: str) -> list[str]`: those with `old < v <= new`, ascending.
  - `maven_changes(path: str, before: str | None, after: str | None) -> list[DependencyChange]`
  - `npm_changes(path: str, before: str | None, after: str | None) -> list[DependencyChange]`: handles both `package.json` and `package-lock.json`, depending on the file name.

- [ ] **Step 1: Write failing tests**

```python
def test_version_key_and_between():
    assert version_key("1.10.0") > version_key("1.9.3")
    assert version_key("2.0.0-rc1") < version_key("2.0.0")
    assert versions_between(["0.7.0","0.8.0","0.8.1","1.0.0"], "0.7.0", "0.8.1") == ["0.8.0", "0.8.1"]
def test_is_major_bump():
    assert is_major_bump("1.4.2", "2.0.0") and is_major_bump("0.7.0", "0.8.0") and not is_major_bump("1.4.2", "1.5.0")

def test_maven_changes(fixtures):
    # pom_before: <properties><okhttp.version>4.9.0</okhttp.version>, deps com.corp:flag-util:1.3.0, okhttp ${okhttp.version}
    #   parent org.springframework.boot:spring-boot-starter-parent:3.2.0
    # pom_after: flag-util removed; dev.openfeature.contrib.providers:flagd:0.8.1 added; okhttp.version 4.12.0;
    #   parent 3.3.0; dependencyManagement import com.corp:platform-bom 2.0.0 (was 1.0.0)
    got = {(c.name, c.kind, c.old_version, c.new_version) for c in maven_changes("svc/pom.xml", before, after)}
    assert got == {
        ("com.corp:flag-util", "removed", "1.3.0", None),
        ("dev.openfeature.contrib.providers:flagd", "added", None, "0.8.1"),
        ("com.squareup.okhttp3:okhttp", "upgraded", "4.9.0", "4.12.0"),
        ("org.springframework.boot:spring-boot-starter-parent", "parent", "3.2.0", "3.3.0"),
        ("com.corp:platform-bom", "bom", "1.0.0", "2.0.0"),
    }

def test_maven_unresolved_property_kept_raw(): ...  # version "${not.defined}" → old/new keep the literal string
def test_maven_new_pom_file(): ...  # before=None → every dependency is "added"

def test_npm_package_json_and_lock(fixtures):
    pj = npm_changes("web/package.json", pkg_before, pkg_after)
    assert ("@openfeature/flagd-web-provider", "added", None, "0.6.0") in {(c.name, c.kind, c.old_version, c.new_version) for c in pj}  # "^0.6.0" stripped
    lock = npm_changes("web/package-lock.json", lock_before, lock_after)
    kinds = {(c.name, c.kind) for c in lock}
    assert ("rxjs", "transitive") in kinds            # 6.6.7 → 7.8.1 major jump, not a direct dep
    assert not any(c.name == "tslib" for c in lock)  # 2.6.1 → 2.6.2 minor transitive: ignored
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **Maven:** use `xml.etree.ElementTree`, handling the `http://maven.apache.org/POM/4.0.0` namespace. Collect `dependencies/dependency` plus `dependencyManagement` entries with `<scope>import</scope>` (these become `bom`), and `parent`. Resolve `${x}` from the same POM's `<properties>` and `${project.version}`.
  - **npm, `package.json`:** compare `dependencies` + `devDependencies`, and strip the leading `^~>=<` from ranges.
  - **npm, lockfile:** use lockfile v2/v3 `packages["node_modules/<name>"].version`. Emit only top-level `node_modules/<name>` entries that are not in the root `packages[""]` deps, and only when `is_major_bump`, as `transitive`. Direct deps are covered by the `package.json` diff.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: version utilities and maven/npm dependency change extraction`

---

### Task 7: Swap detection

**Files:**
- Create: `src/scope_agent/deps/swaps.py`
- Test: `tests/deps/test_swaps.py`

**Interfaces:**
- Consumes: `DependencyChange`, `CommitData`
- Produces:
  - `Swap(removed: DependencyChange, added: DependencyChange, reason: str)`
  - `detect_swaps(changes: list[DependencyChange], commits: list[CommitData], known_swaps: list[tuple[str, str]]) -> list[Swap]`

A removed and an added dependency in the same ecosystem are paired, first match wins, in this order:
1. Listed in `known_swaps`.
2. Their name tokens share a token that isn't generic. Split on `[-.:_/@]`, lower-case, and drop these generic tokens: `com, org, io, dev, net, java, js, core, client, lib, util, utils, api, sdk, contrib, providers, provider, starter, common, web, corp`.
3. Some file in the commits has a removed line importing the removed lib's prefix and an added line importing the added lib's prefix. The prefix is the Maven groupId or the npm package name, matched on lines starting with `import ` or containing `from '`.

- [ ] **Step 1: Write failing tests**

```python
def test_no_swap_without_evidence():
    removed = dc("com.corp:flag-util", "removed"); added = dc("dev.openfeature.contrib.providers:flagd", "added")
    # "flag" vs "flagd": tokens differ, no imports, not configured → not a swap
    assert detect_swaps([removed, added], [], []) == []
def test_swap_by_import_evidence():
    commit = commit_with_diff("src/Flags.java", before="import com.corp.flagutil.FlagClient;", after="import dev.openfeature.contrib.providers.flagd.FlagdProvider;")
    swaps = detect_swaps([dc("com.corp:flag-util","removed"), dc("dev.openfeature.contrib.providers:flagd","added")], [commit], [])
    assert len(swaps) == 1 and swaps[0].reason == "import_change"
def test_swap_known_pair():
    assert detect_swaps([dc("a:x","removed"), dc("b:y","added")], [], [("a:x","b:y")])[0].reason == "configured"
def test_swap_token_overlap_npm():
    assert detect_swaps([dc("ngx-feature-toggle","removed", eco="npm"), dc("@openfeature/feature-toggle-web","added", eco="npm")], [], [])[0].reason == "name_overlap"
def test_no_swap_across_ecosystems(): ...
```

For the Maven import rule, the removed lib's import prefix uses the groupId (`com.corp`) **and** the groupId plus the artifactId with dashes removed (`com.corp.flagutil`). Either one counts.

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. Use `difflib` over `FileChange.before/after` lines to find the removed and added import lines.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: dependency swap detection`

---

### Task 8: Source resolution and release notes / docs fetching

**Files:**
- Create: `src/scope_agent/deps/sources.py`
- Test: `tests/deps/test_sources.py` (respx)

**Interfaces:**
- Consumes: `Settings`, `AppConfig.library_docs`, `AppConfig.internal_repos`, `BitbucketClient`, `Store` (cache), `versions_between`
- Produces:
  - `SourceRepo(kind: Literal["github","bitbucket","unknown"], ref: str, subpath: str | None)`. `ref` is `"owner/name"` for GitHub and `"PROJ/slug"` for Bitbucket.
  - `ReleaseNotes(library, versions: list[str], entries: list[tuple[str, str]], links: list[str], available: bool)`. Each entry is `(version, text)`.
  - `LibraryDocs(library, version, text, links: list[str])`. `text` is capped at 40 000 chars.
  - `SourceResolver(settings, cfg, http, store)` with `resolve(change: DependencyChange) -> SourceRepo`:
    - `internal_repos` mapping first;
    - then for Maven, the Nexus POM `GET {nexus}/repository/{maven_repo}/{group/as/path}/{artifact}/{version}/{artifact}-{version}.pom`, using `<scm><url>` or `<url>`;
    - for npm, `GET {nexus}/repository/{npm_repo}/{name}` → `repository.url` (+ `repository.directory` → subpath).
    - A host equal to the Bitbucket host means `bitbucket` (parse `/projects/P/repos/r` or `/scm/p/r.git`). `github.com` means `github`.
  - `ReleaseNotesFetcher(resolver, bitbucket, settings, http, store)` with two methods:
    - `notes_between(change) -> ReleaseNotes`
    - `docs_for(name, ecosystem, version) -> LibraryDocs`

**`notes_between` rules:**
- **GitHub:** list `GET /repos/{o}/{r}/releases?per_page=100`, up to 5 pages. The tag's version is the trailing match of `(\d+(?:\.\d+)+(?:[-.][0-9A-Za-z.]+)?)$`. In a monorepo, some tags contain the package hint (the Maven artifactId, or the npm name without its scope); when any do, only those tags are used. Keep the versions in `versions_between(old, new)`.
- **GitHub fallback:** if there are no releases, read `CHANGELOG.md`, `CHANGES.md` or `RELEASE_NOTES.md` (under `subpath` first) via `GET /repos/{o}/{r}/contents/{file}`. Split it on headings that contain a version, and keep the in-range sections.
- **Bitbucket (in-house):** find the tags for old and new (try `v{ver}`, `{ver}`, `{artifact}-{ver}`). The entries are `commit_messages_between(tag_old, tag_new)` joined, as a single entry `(new_version, text)`.
- **Results:** `available=False` when nothing is found or on HTTP 403/429/404. Cache the result permanently under the key `f"{name}@{old}..{new}"`.

**`docs_for` sources:** the GitHub README (`GET /repos/{o}/{r}/readme`, raw media type, under `subpath` if present) plus each URL in `library_docs[name]`. Fetch those with HTML stripped via `html.parser`. For Bitbucket sources, use `README.md` at the tag.

- [ ] **Step 1: Write failing tests**
  - `test_resolve_maven_via_nexus_scm_github`: the POM has `<scm><url>https://github.com/open-feature/java-sdk-contrib</url></scm>`, resolving to `SourceRepo("github","open-feature/java-sdk-contrib",None)`.
  - `test_resolve_internal_mapping_wins`
  - `test_resolve_bitbucket_scm_url`: `https://bitbucket.corp/scm/plat/flag-util.git` resolves to `("bitbucket","PLAT/flag-util")`.
  - `test_notes_between_monorepo_tags`: the releases are tagged `dev.openfeature.contrib.providers.flagd-v0.8.0`, `…flagd-v0.8.1`, and `…ofrep-v0.1.0`. For old `0.7.0` and new `0.8.1`, the entries' versions are `["0.8.0","0.8.1"]`.
  - `test_notes_between_changelog_fallback`
  - `test_notes_between_rate_limited_marks_unavailable`: 403 means `available is False`. *(Review Focus 3.)*
  - `test_notes_between_internal_bitbucket_commit_messages`
  - `test_docs_for_readme_plus_override_url`: includes the override text, with HTML tags stripped.
  - `test_notes_cached`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. Send the GitHub token as a header when it's set. The Nexus auth is basic.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: source resolution, release notes and library docs fetching`

---

### Task 9: LLM client, response schemas and budget

**Files:**
- Create: `src/scope_agent/llm/__init__.py`, `src/scope_agent/llm/client.py`, `src/scope_agent/llm/prompts/{dependency_upgrade,dependency_new,dependency_swap,code_review}.j2`
- Test: `tests/llm/test_client.py`

**Interfaces:**
- Consumes: `Store` (tokens), `LLMConfig`
- Produces:
  - `LLMFinding(BaseModel)`: `type: FindingType`, `category: str`, `severity: Severity`, `title: str`, `explanation: str`, `evidence: list[str]`, `files: list[str] = []`
  - `DependencyVerdict(findings: list[LLMFinding], reviewer_summary: str)`
  - `CodeVerdict(findings: list[LLMFinding], ac_quality: Literal["ok","vague","missing"], ac_quality_reason: str)`
  - `LLMResult(data: BaseModel, input_tokens: int, output_tokens: int)`
  - `class LLMClient(Protocol): def analyze(self, system: str, prompt: str, schema: type[BaseModel]) -> LLMResult`
  - `ClaudeClient(api_key, model, max_tokens=4096)`: forces a single tool `report` whose `input_schema = schema.model_json_schema()` with `tool_choice={"type":"tool","name":"report"}`, then validates with `schema.model_validate`. On a `ValidationError` or a missing tool call, it retries **once** with the validation error appended, then raises `LLMOutputError`.
  - `BudgetGuard(inner: LLMClient, store: Store, daily_budget: int, clock)`: also an `LLMClient`. It raises `BudgetExceeded` **before** the call when `tokens_used(today) >= daily_budget`, and records `input+output` tokens after.
  - `render_prompt(name: str, **ctx) -> str`, using Jinja2 `PackageLoader("scope_agent.llm", "prompts")`
  - `RISK_CATEGORIES: tuple[str, ...]`:
    - runtime: `connection_model`, `connection_pool`, `retries_timeouts`, `caching`, `resource_usage`, `tls_security`, `serialization`, `config_removed`, `concurrency`
    - change type: `schema_migration`, `auth_config`, `external_call`, `feature_flag_defaults`, `infra_config`
    - dependency: `new_dependency`, `platform_upgrade`, `behavior_unknown`, `transitive_major`
    
    This list is listed in every prompt.

**Prompt content requirements** (wording is the builder's call):
- **Every prompt** says the output is for human review, gives the category list, and says to quote evidence verbatim.
- `dependency_upgrade`: given the release-notes entries and usage context, report changed defaults or runtime behavior from spec §6.4 step 3. Raise severity when the usage context relies on defaults. `reviewer_summary` is ≤ 120 words.
- `dependency_new`: given the docs, summarize the runtime defaults (connection model, ports, retries, keep-alive, caching) in `reviewer_summary`. Flag network, IO or concurrency libraries at medium or above.
- `dependency_swap`: given the old library profile (source excerpts or usage) and the new library docs, compare connection model, connection count and keep-alive, ports and endpoints, retries, caching, and failure behavior. **A connection model or connection count difference is `high` with category `connection_model`.**
- `code_review`: given the ticket, ACs, Epic and diff chunk:
  - Report `risky_change` and `out_of_scope` findings. Neighboring cleanup is fine; formatting-only or test-only changes are never out of scope.
  - Rate `ac_quality`: `vague` means not testable or observable.

- [ ] **Step 1: Write failing tests** (fake the anthropic client object)
  - `test_claude_client_parses_tool_call`
  - `test_claude_client_retries_once_then_raises`: the first two responses are invalid, so it raises `LLMOutputError` and the fake was called twice. *(Review Focus 2.)*
  - `test_budget_guard_blocks_when_exhausted`: budget 100 with 100 used raises `BudgetExceeded`, and the inner client is never called.
  - `test_budget_guard_records_usage`
  - `test_render_prompt_includes_categories`: `"connection_model" in render_prompt("dependency_swap", ...)`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: llm client interface, claude implementation, budget guard and prompts`

---

### Task 10: Dependency analysis

**Files:**
- Create: `src/scope_agent/analysis/__init__.py`, `src/scope_agent/analysis/dependency.py`
- Test: `tests/analysis/test_dependency.py` (fake `LLMClient`, fake fetcher)

**Interfaces:**
- Consumes: `DependencyChange`, `Swap`, `detect_swaps`, `ReleaseNotesFetcher`, `LLMClient`, `render_prompt`, `is_major_bump`, `CommitData`, `BitbucketClient.get_file`/`list_files`
- Produces:
  - `DependencyAnalyzer(llm, fetcher, bitbucket, cfg)` with `analyze(commits: list[CommitData], changes_by_commit: dict[str, list[DependencyChange]]) -> tuple[list[Finding], dict[str, list[LibrarySummary]], list[str]]`. It returns the findings, the summaries keyed by commit SHA, and the errors.
  - `usage_context(commit: CommitData, change: DependencyChange, bitbucket) -> str`: diff hunks of files that mention the library's prefix, plus the content of up to 5 files matching `**/application*.y*ml` / `**/application*.properties` / `**/environment*.ts` at the commit. Capped at 20 000 chars.

**Per-change rules:**

| kind | action | finding defaults |
|---|---|---|
| part of a `Swap` | `dependency_swap` prompt. Old profile: internal source (Bitbucket README + up to 8 files whose path or content matches `poll|schedul|interval|stream|grpc|websocket|http|connect|client`, 30 000 chars total) if resolvable, else `usage_context` of the removed side. New side: `docs_for` | attach to the commit that added the new lib |
| `upgraded`/`downgraded` | `notes_between`. If `available`, use the `dependency_upgrade` prompt. Otherwise **no LLM call**, emit `behavior_unknown` | unknown: `medium` if `is_major_bump` else `low` |
| `parent`/`bom` | always emit `platform_upgrade` (`medium`, "many managed versions changed"), plus the upgrade analysis of its own notes | |
| `added` (not a swap) | `docs_for` + `dependency_new` prompt | |
| `transitive` | emit `transitive_major` (`low`), no LLM | |
| `removed` (not a swap) | nothing | |

- Every LLM finding becomes a `Finding` with `type=dependency_behavior_change`. The release-notes or doc links are appended to `evidence`.
- `reviewer_summary` becomes a `LibrarySummary` (kind: `upgrade|new|swap`) for every external upgraded, added or swapped library, **even when no finding is raised**.
- `LLMOutputError` on one change: append it to the errors list and continue with the other changes.

- [ ] **Step 1: Write failing tests**
  - `test_swap_yields_high_connection_model_finding`: the fake LLM returns a high `connection_model` finding for the swap prompt. The test asserts a finding with `severity == "high"`, `category == "connection_model"`, attached to the adding commit, with a `LibrarySummary(kind="swap")`.
  - `test_upgrade_without_notes_emits_behavior_unknown_and_no_llm_call`: `1.4.0 → 2.0.0` gives `medium`, and `1.4.0 → 1.5.0` gives `low`. The fake LLM's call count is 0.
  - `test_parent_bump_emits_platform_upgrade`
  - `test_transitive_major_low_no_llm`
  - `test_added_library_produces_summary_even_without_findings`
  - `test_llm_error_on_one_change_does_not_stop_others`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: dependency upgrade, new-library and swap analysis`

---

### Task 11: Code risk and scope analysis

**Files:**
- Create: `src/scope_agent/analysis/code.py`
- Test: `tests/analysis/test_code.py`

**Interfaces:**
- Consumes: `CommitData`, `TicketContext`, `LLMClient`, `CodeVerdict`, `FilesConfig`, `LLMConfig.max_input_tokens_per_call`
- Produces:
  - `select_files(files: list[FileChange], cfg: FilesConfig) -> list[FileChange]`: drops `exclude` globs and binary/oversize files (`before is None and after is None`). Order: `priority` matches first, then the rest by diff size ascending.
  - `unified_diff(fc: FileChange) -> str`, using `difflib.unified_diff` with 3 context lines
  - `chunk(diffs: list[tuple[str, str]], max_tokens: int) -> tuple[list[list[tuple[str, str]]], list[str]]` returns the chunks and the skipped paths. Tokens are estimated as `len(text) // 4`. The ticket context gets a reserve of 25%. **At most 3 chunks per commit**, and the remainder is skipped.
  - `CodeAnalyzer(llm, cfg)` with `analyze(commit: CommitData, tickets: list[TicketContext]) -> tuple[list[Finding], Literal["ok","vague","missing"] | None, list[str]]`. It returns the findings, the worst `ac_quality` across chunks (`None` when there are no tickets), and the errors.
  - When paths were skipped, it adds an `info` finding: `type=risky_change`, `category="partial_analysis"`, with the skipped paths in `evidence`.
  - With no tickets, it still runs. The prompt states "no ticket linked", and out-of-scope findings are dropped because there is nothing to compare against.

- [ ] **Step 1: Write failing tests**
  - `test_select_files_excludes_and_prioritises`: `package-lock.json` is dropped, and `src/main/resources/application.yml` comes first.
  - `test_chunk_respects_budget_and_reports_skipped`: 5 diffs of 40 000 chars each with `max_tokens=60_000` give ≤ 3 chunks, and the skipped list is non-empty.
  - `test_partial_analysis_finding_added`: *(Review Focus 5.)*
  - `test_no_ticket_drops_out_of_scope`
  - `test_worst_ac_quality_wins`: the chunks return `ok` and `vague`, so the result is `vague`.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: code risk and scope analysis with file prioritisation and chunking`

---

### Task 12: Analysis pipeline (push → findings)

**Files:**
- Create: `src/scope_agent/analysis/pipeline.py`
- Test: `tests/analysis/test_pipeline.py` (fakes for Bitbucket, JIRA, the analyzers and the notifier)

**Interfaces:**
- Consumes: `BitbucketClient`, `JiraClient`, `TicketNotFound`, `extract_keys`, `maven_changes`, `npm_changes`, `DependencyAnalyzer`, `CodeAnalyzer`, `Store`, `BudgetExceeded`
- Produces:
  - `PushResult(job: PushJob, commits: list[CommitData], findings: list[Finding], summaries: dict[str, list[LibrarySummary]])`
  - `Pipeline(bitbucket, jira, dep_analyzer, code_analyzer, store, cfg)` with `run(job_id: int, job: PushJob) -> PushResult`

**Steps:**
1. `commits_in_push(job, cfg.max_commits_per_push)`, then drop SHAs already `is_analyzed` and merge commits (still `mark_analyzed` the merges).
2. For each commit, `get_commit`, then `extract_keys(message, job.branch, project_keys)`. Fetch tickets once per key per push.
3. **Ticket gaps** (`type=ticket_gap`):

   | condition | category | severity |
   |---|---|---|
   | no key | `missing_key` | `low` |
   | `TicketNotFound` | `ticket_not_found` | `info` |
   | `acceptance_criteria is None` | `missing_ac` | `info` |
   | code analyzer `ac_quality == "vague"` | `vague_ac` | `info` |
   | no Epic | `no_epic` | `info` |

   Gaps are raised once per ticket per push.
4. **Dependency changes:** for every `FileChange` whose basename is `pom.xml`, `package.json` or `package-lock.json`. Then `DependencyAnalyzer.analyze` over all of the push's commits together, so swaps across commits in one push are detected.
5. `CodeAnalyzer.analyze` per commit.
6. Fill in `repo`, `commit`, `author_email` and `jira_keys` on every finding. `save_findings` and `save_summaries`, `record_error` for each error, then `mark_analyzed` each commit. **All of this goes in one transaction at the end.**
7. **`BudgetExceeded` propagates** and nothing is persisted. The worker defers the job, so the commits stay un-analyzed and are retried. *(Review Focus 1: a redelivered webhook with the same `to_hash` is deduped by `enqueue`. A commit already analyzed via `development` is skipped on `release/*`.)*

- [ ] **Step 1: Write failing tests**
  - `test_skips_already_analyzed_and_merge_commits`
  - `test_missing_key_creates_low_ticket_gap_and_still_runs_code_analysis`
  - `test_ticket_not_found_and_missing_ac_and_no_epic_gaps_are_info`
  - `test_vague_ac_gap_raised_once_per_ticket`
  - `test_dependency_files_routed_to_extractors`: `svc/pom.xml` goes to `maven_changes`, and `web/package.json` goes to `npm_changes`.
  - `test_findings_enriched_and_commits_marked`
  - `test_budget_exceeded_persists_nothing`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: push analysis pipeline with ticket gap detection`

---

### Task 13: Recipient routing

**Files:**
- Create: `src/scope_agent/notify/__init__.py`, `src/scope_agent/notify/routing.py`
- Test: `tests/notify/test_routing.py`

**Interfaces:**
- Consumes: `RoutingConfig`, `Finding`, `CommitData`
- Produces:
  - `reviewers_for(finding: Finding, routing: RoutingConfig) -> list[str]`: the repo leads (the key is the slug or `"PROJ/slug"`), plus owners of paths matched by `fnmatch` against `finding.files`, plus `categories[finding.category]`. It falls back to `default_leads` when there are no repo or path leads. The result is lower-cased, deduped, and in order.
  - `push_recipients(findings: list[Finding], commits: list[CommitData], routing) -> list[str]`: the author emails of the flagged commits, plus the union of `reviewers_for`.

- [ ] **Step 1: Write failing tests**
  - `test_repo_and_path_and_category_owners`: a `connection_model` finding on `src/main/java/x/flags/F.java` → `["lead-a@corp","flags-owner@corp","gateway-owner@corp","platform-sre@corp"]`.
  - `test_unknown_repo_falls_back_to_default_leads`: *(Review Focus 4.)*
  - `test_missing_author_email_still_returns_leads`
  - `test_slug_or_project_slug_key`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement.** Before sending, the worker (Task 15) writes `reviewers_for` into each finding's `suggested_reviewers`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: recipient routing for findings`

---

### Task 14: Emails (push + digest), rating links, SMTP

**Files:**
- Create: `src/scope_agent/notify/email.py`, `src/scope_agent/notify/templates/{push.html.j2,push.txt.j2,digest.html.j2,digest.txt.j2}`
- Test: `tests/notify/test_email.py`

**Interfaces:**
- Consumes: `PushResult`, `Finding`, `LibrarySummary`, `Store`, `push_recipients`, `Settings`
- Produces:
  - `rating_token(secret, finding_id, value) -> str` (HMAC-SHA256 hex, first 32 chars) and `rating_url(base, secret, finding_id, value) -> str` → `{base}/rate/{id}?v={value}&t={token}`
  - `should_send_push(result: PushResult) -> bool`: true if any finding has severity ≥ `low`, **or** any `LibrarySummary` exists (decided: a library change always gives the lead context)
  - `render_push(result, settings) -> EmailMessage`
    - Subject: `[Scope Agent] {n} item(s) for review: {repo} {branch} ({first7}..{last7})`
    - Body: findings with severity ≥ `low`, sorted high-first. Each shows title, why it was flagged (the explanation), evidence, the commit link `{bitbucket_url}/projects/{P}/repos/{r}/commits/{sha}`, ticket links `{jira_url}/browse/{key}`, suggested reviewers, and the Useful/Noise links.
    - Then a "Library changes" section with the summaries.
    - Footer: "This is an automated review aid. Nothing has been blocked."
  - `render_digest(findings, errors: int, deferred: int, period_start, period_end, settings) -> EmailMessage`
    - Subject: `[Scope Agent] Daily digest {YYYY-MM-DD}: {high} high, {total} findings`
    - Body: grouped by repo, then ticket (or "No ticket"), high first. Includes all severities, including `info`.
    - Ticket-gap stats: commits without a key, tickets with missing ACs, tickets with vague ACs.
    - Analysis error count and deferred job count.
  - `SmtpSender(settings)` with `send(msg: EmailMessage, to: list[str])` via STARTTLS. With no recipients it logs and returns.

- [ ] **Step 1: Write failing tests**
  - `test_should_send_push_rules`: only `info` findings with no summaries → False; one summary → True.
  - `test_push_email_subject_order_and_links`: the subject matches exactly, the high finding appears before the low one in the text body, and contains `/rate/{id}?v=useful&t=` and `/browse/PAY-12`.
  - `test_push_email_excludes_info`
  - `test_digest_groups_and_counts`: the subject says `2 high, 5 findings`, and the body contains `Analysis errors: 1` and `Deferred (budget): 2`.
  - `test_rating_token_stable`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** with multipart text and HTML via Jinja2 `PackageLoader("scope_agent.notify", "templates")`.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: push and digest emails with rating links`

---

### Task 15: Web app, worker and digest scheduler

**Files:**
- Create: `src/scope_agent/web.py`, `src/scope_agent/worker.py`, `src/scope_agent/scheduler.py`
- Test: `tests/test_web.py` (FastAPI `TestClient`), `tests/test_worker.py`, `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `verify_signature`, `parse_refs_changed`, `Store`, `Pipeline`, `reviewers_for`, `push_recipients`, `should_send_push`, `render_push`, `render_digest`, `SmtpSender`, `rating_token`, `BudgetExceeded`
- Produces:
  - `create_app(settings, cfg, store) -> FastAPI` with these routes:
    - `POST /webhook/bitbucket`: `X-Event-Key: diagnostics:ping` → 200. A bad signature → 401. `repo:refs_changed` → enqueue each `PushJob` with dedup key `f"{repo}:{branch}:{to_hash}"` → 202 `{"queued": n}`. Other events → 204.
    - `GET /rate/{finding_id}?v=&t=`: a valid token → `save_rating` → 200 HTML "Thanks, recorded." A bad token → 403.
    - `GET /healthz`: 200.
  - `Worker(store, pipeline, routing, sender, settings, clock)` with:
    - `run_once() -> bool` (False when idle)
    - `run_forever(stop: threading.Event, poll_seconds=2)`
    
    **Handling in `run_once`:**
    - claim a job and run the pipeline;
    - fill in `suggested_reviewers`;
    - send the push email if `should_send_push`;
    - `complete`.
    
    **Failure handling:**
    - `BudgetExceeded` → `defer(until=next midnight in digest timezone + 5 min)`.
    - Any other exception → `fail(retry_at=now + [30 s, 120 s, 600 s][attempts-1])`. After 3 attempts, `fail(retry_at=None)` and `record_error`.
  - `send_digest(store, sender, settings, cfg, now)`: covers the period since `meta["last_digest_at"]`, or `now - 24h`, up to `now`, then sets the meta value.
  - `start_scheduler(...) -> BackgroundScheduler`: a cron trigger at `cfg.digest.time` in `cfg.digest.timezone`.

- [ ] **Step 1: Write failing tests**
  - `test_webhook_rejects_bad_signature`
  - `test_webhook_ping_ok`
  - `test_webhook_enqueues_and_dedups_redelivery`: the same body posted twice gives `queued` of 2 and then 0. *(Review Focus 1.)*
  - `test_rate_endpoint_valid_and_invalid_token`
  - `test_worker_sends_email_and_completes`
  - `test_worker_defers_on_budget_exceeded`
  - `test_worker_retries_then_fails_and_records_error`
  - `test_worker_survives_pipeline_exception`: after a failure, `run_once` can still process the next job. *(Review Focus 2.)*
  - `test_send_digest_uses_last_digest_window`
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** as specified. The webhook reads the raw body (`await request.body()`) **before** JSON parsing, for the signature check.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: webhook app, background worker and digest scheduler`

---

### Task 16: CLI, wiring, backtest/report and incident replay

**Files:**
- Create: `src/scope_agent/cli.py`, `src/scope_agent/wiring.py`, `README.md`
- Test: `tests/test_cli.py`, `tests/test_incident_replay.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `build_container(settings, cfg) -> Container`: a dataclass holding `store, bitbucket, jira, fetcher, llm (BudgetGuard(ClaudeClient)), pipeline, routing, sender`. It's the single place where concrete classes are constructed.
  - The typer `app` has these commands:
    - `serve [--host 0.0.0.0 --port 8080]`: uvicorn with `create_app`, the worker thread and the scheduler.
    - `analyze REPO SHA [--send-email/--no-send-email (default no)] [--json]`: builds a synthetic `PushJob(from_hash=parent, to_hash=SHA)` and runs `Pipeline` with **dedup bypassed** (`force=True`, so `Pipeline.run` gains a `force: bool = False` that skips the `is_analyzed` filter). It prints findings as a table or JSON.
    - `backtest REPO --branch development --limit 200 [--send-email/--no-send-email (default yes, so leads can rate)]`: the last N non-merge commits on the branch, each analyzed like `analyze`, with findings persisted.
    - `digest --now`
    - `report`: prints `rating_report()` with the useful rate per type × severity and overall for high+medium, against the 70% target.

**README** covers:
- installation and env vars;
- `config.yaml` / `routing.yaml`;
- creating the Bitbucket DC webhook: event "Repository → Push", the secret, and URL `{host}/webhook/bitbucket`;
- the JIRA Cloud API token, and the Nexus read-only account;
- the **manual incident replay**: `scope-agent analyze PAY/<repo> <swap-commit-sha> --json` must show a `high` `connection_model` finding citing the flagd docs. Add the flagd provider docs URL to `library_docs` for the flagd artifact before running.

- [ ] **Step 1: Write failing tests**
  - `test_analyze_command_prints_json`: the container is replaced with fakes via a monkeypatched `build_container`.
  - `test_report_command_computes_useful_rate`
  - `test_incident_replay_wiring` (end-to-end with fakes, no network):
    - a commit whose `pom.xml` removes `com.corp:flag-util:1.3.0` and adds `dev.openfeature.contrib.providers:flagd:0.8.1`;
    - its `Flags.java` changes imports from `com.corp.flagutil` to `dev.openfeature.contrib.providers.flagd`;
    - the fake LLM returns a high `connection_model` finding for the swap prompt.
    
    It asserts:
    - one push email;
    - the recipients include `gateway-owner@corp` (from routing `categories.connection_model`);
    - the email text contains "connection_model" or the finding title and a "Library changes" section for flagd.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `wiring.py`, `cli.py` and the `force` flag, and write the README.
- [ ] **Step 4: Run** the full suite: `pytest -v`. Expected: all PASS.
- [ ] **Step 5: Commit** `feat: CLI, wiring, backtest/report and incident replay test`
