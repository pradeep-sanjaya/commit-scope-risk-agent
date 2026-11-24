# Commit Scope & Risk Agent — Design (POC)

Date: 2026-11-23
Status: Draft for approval

## 1. Intent and why

Unplanned, unreviewed changes are reaching releases. The motivating incident: the
feature-flag client was **replaced** — an in-house utility dependency (which polled)
was swapped for the flagd-recommended (OpenFeature flagd) library, whose default
connection model is **long-lived streaming**. The JIRA ticket said only "implement
feature flag client"; developers didn't know the protocol had changed. Long-lived streaming connections
exhausted connections at the API gateway and the Envoy layer, causing a
**~10-hour outage**.

The agent's job is to surface changes like that **for human review**, shortly after
they are pushed, by:

1. Detecting risky runtime/operational changes in the diff — including changes that
   arrive through dependency upgrades, where the danger is in the library's release
   notes, not in the one-line version bump.
2. Comparing the work against the JIRA ticket, its acceptance criteria (ACs) and its
   Epic, and flagging work the ticket didn't plan for.
3. Flagging ticket-quality gaps (vague/missing ACs, missing JIRA key) so PMO can
   improve ticket hygiene.

## 2. Goals, non-goals, anti-goals

**Goals**
- Analyze every pushed commit on Bitbucket Data Center within minutes.
- Catch documented behavior changes in Maven and npm (Angular) dependency upgrades,
  citing the exact release-note text.
- Notify the commit author and the relevant module lead(s) per push when findings exist.
- Send PMO one consolidated daily digest of all findings.
- Keep the LLM behind an interface so the hosted model can be swapped for a local
  model after the POC.

**Non-goals (POC)**
- Blocking merges or pushes. The agent is **report-only**.
- Other ecosystems (Gradle, pip, Go, …) — later.
- Full Maven transitive-dependency resolution (running `mvn dependency:tree`). See §6.4.
- Pull-request comments / Code Insights annotations — possible later addition.
- A UI. Email + a database the team can query is enough for the POC.

**Anti-goals (failure even if it "works")**
- **Noise.** If people learn to ignore the emails, the tool has failed. Precision
  matters more than recall for code-pattern findings; dependency behavior changes
  are the exception (see §6.4).
- **Blame/surveillance tone.** Emails are framed as "for review", neutral, with
  evidence — not as accusations.
- **Slowing developers.** Nothing in the developer's workflow waits on the agent.

## 3. Constraints

- Bitbucket **Data Center (self-managed)**. Webhook event `repo:refs_changed`;
  REST API 1.0; HTTP access tokens. The service runs on the internal network where
  Bitbucket can reach it — no public exposure needed.
- Sending code to a hosted LLM is **approved for the POC**. The service needs
  outbound internet access to the LLM API, GitHub, npm registry and Maven Central.
- First stacks: **Maven (Java backends)** and **npm (Angular frontends)**.
- Dependencies are served through an **internal Nexus** repository. The agent reads
  Maven POMs and npm package metadata from Nexus, using a read-only Nexus account.
  This resolves internal parent POMs, BOMs and version properties as well as proxied
  public artifacts. For **in-house libraries**, the source repo in Bitbucket is found
  from the POM's `<scm>` element or `package.json` `repository` field. A configurable
  `groupId`/package → Bitbucket repo mapping is the fallback. The agent then reads
  that library's commit history and source between versions, instead of public
  release notes.
- Multiple repos across the organization.
- **JIRA Cloud** (Bitbucket itself is self-managed).
- Push volume is unknown. *Builder's call:* SQLite and a single worker for the POC,
  plus a configurable daily LLM token budget. When the budget is hit, remaining
  pushes are queued and reported in the digest, never silently dropped.

## 3a. Rating findings (builder's call)
Every finding in an email has "useful" / "noise" links that record the rating in
the findings database. A small report computes the useful rate per severity and
type, to measure the 70% target in §7.

## 4. Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| A. Nightly batch: poll Bitbucket once a day, analyze, digest | Least infrastructure, groups WIP commits per ticket; but no timely per-commit feedback to authors | Rejected — user wants per-commit notification to author and lead |
| **B. Central webhook service**: one service receives push webhooks from all repos, analyzes each push, emails author + lead, sends PMO daily digest | Timely feedback, one place to configure, works on DC, ready for later PR annotations; must host a service plus a job queue | **Chosen by user** |
| C. Bitbucket Pipelines step per repo | No hosting, but per-repo config/secrets, pipeline minutes, and Bitbucket Cloud only | Rejected — DC, many repos |

Notification policy decided by user: **report only**; per-push email to **author and
lead**; **daily digest to PMO**.

## 5. Architecture

```
Bitbucket DC ──(repo:refs_changed webhook, HMAC-signed)──▶  Webhook receiver
                                                              │ verify, enqueue, 202
                                                              ▼
                                                        Job queue (DB table)
                                                              │
                                                              ▼
                                                         Analysis worker
     ┌──────────────┬──────────────┬──────────────────┬──────┴───────┐
     ▼              ▼              ▼                  ▼              ▼
 Bitbucket API   JIRA API    Dependency resolver   LLM client     Findings store
 (commits,diff,  (ticket,    (Maven/npm versions,  (interface:    (DB)
  file content)  epic, ACs)  release notes)        hosted→local)
                                                                    │
                                     ┌──────────────────────────────┤
                                     ▼                              ▼
                          Per-push email (author+lead)   Daily PMO digest (scheduler)
```

**Tech choices (builder's call, proposed):** Python 3.12, FastAPI for the webhook
receiver, a DB-backed job table with a background worker (survives restarts),
**PostgreSQL** as the backing store (SQLite only for tests and local dev), SMTP for
email, Jinja2 templates for email bodies. Anthropic Claude as the hosted LLM for the
POC (model configurable; default a current Sonnet-class model).

### 5.1 Twelve-factor and single-responsibility rules (decided by user)
- **Config:** *all* configuration comes from environment variables (`SCOPE_*`), with
  nothing hard-coded and no config file baked into the image. Larger structured data
  is kept in files **whose paths come from env vars**, mounted at deploy time (for
  example a ConfigMap):
  - routing (who gets notified)
  - dependency hints (doc URLs, in-house library → repo mapping, known swaps)

  Every setting is documented in `.env.example`.
- **Backing services** are attached via URLs or credentials in env: the database
  (`SCOPE_DATABASE_URL`), SMTP, the LLM endpoint, Bitbucket, JIRA, Nexus and GitHub.
  Swapping hosted → local LLM is an env change.
- **Process types:**
  - `web` (webhook + rating links, binds `$PORT`)
  - `worker` (analysis; horizontally scalable, jobs claimed with
    `FOR UPDATE SKIP LOCKED`)
  - `digest` (a one-off admin process run by the platform's scheduler, e.g. a
    K8s CronJob or cron)

  There's no in-process scheduler.
- **Stateless and disposable processes:** all state lives in the database. Workers
  finish or release their job on SIGTERM. A job left `running` by a crashed worker
  is reclaimed after a lease timeout.
- **Logs:** structured JSON to stdout only.
- **Build, release, run:** one container image. A release is image + env.
- **Admin processes:** `migrate`, `analyze`, `backtest`, `report` and `digest` run as
  one-off commands from the same image.
- **Dev/prod parity:** docker-compose with Postgres and a local SMTP catcher.
- **Single responsibility:** each module has one reason to change.
  - Integrations (Bitbucket, JIRA, Nexus, GitHub, LLM, SMTP) are thin adapters with
    no business rules.
  - Business rules (swap detection, gap detection, severity, routing) are pure
    functions or classes with no I/O.
  - Orchestration (pipeline, worker, notifier) only wires them together.
  - Persistence is split into one repository per aggregate.
  - Objects are built in a single composition root.

## 6. Behavior

### 6.1 Trigger and intake
- Webhook on `repo:refs_changed`, configured per project or per repo in Bitbucket.
- Verify the HMAC signature (`X-Hub-Signature`, shared secret). Reject unsigned.
- **Branch filter (decided by user):** only pushes to `development`, `hotfix/*` and
  `release/*` are analyzed, configurable as glob patterns. Pushes to any other
  branch are ignored. Feature-branch work is analyzed when it lands on
  `development`, because its commits are new to that branch; squash merges arrive
  as one commit.
- Ignore ref deletions and tag pushes. Force-pushes: analyze only commits not
  already analyzed.
- Resolve new commits for each change via
  `GET /rest/api/1.0/projects/{p}/repos/{r}/commits?since={fromHash}&until={toHash}`.
  For a new branch (`fromHash` all zeros), compare against the repo's default branch.
- **Dedup by commit hash per repo** — the same commit pushed to several branches is
  analyzed once.
- Skip merge commits (more than one parent).
- Respond 202 immediately; analysis is asynchronous with retries and backoff.

### 6.2 JIRA key extraction
- Regex `[A-Z][A-Z0-9]+-\d+` over the commit message (conventional-commit scope,
  subject, body or footer). Fall back to the branch name (e.g. `feature/PROJ-123-…`).
- Multiple keys → analyze against all of them together.
- Validate keys against a configured list of JIRA project keys, to avoid false
  matches like `UTF-8`.
- No key → a **ticket-gap** finding; still run the risk analysis (§6.4–6.5).

### 6.3 Ticket context
**JIRA Cloud** (decided by user), REST API v3, using a service account's email +
API token (basic auth). Fetch: ticket summary, description, type, status, ACs,
components, labels; the parent **Epic's** summary and description; linked issues
(titles only).
- Description and rich-text fields come back as Atlassian Document Format (ADF)
  JSON, so convert them to plain text or Markdown before prompting.
- AC location is configurable: a custom field ID, or a heading in the description
  ("Acceptance Criteria").
- Epic resolution: the `parent` field (standard on JIRA Cloud). If the ticket is a
  sub-task, walk up to its parent's Epic.
- Cache tickets for a short time (e.g. 10 min) to avoid repeat calls during bursts.
- **Ticket-gap findings:** missing ACs; ACs judged vague by the LLM (not testable or
  observable); no Epic; key not found or not accessible.

### 6.4 Dependency change analysis (the core feature)
Triggered when the commit touches `pom.xml` (any module) or `package.json` /
`package-lock.json`.

1. **Extract version changes** by diffing the file before and after the commit:
   - Maven: direct `<dependency>` versions, `<properties>` used as versions,
     `<parent>` version, `<dependencyManagement>` BOM imports. A parent or BOM
     bump (e.g. `spring-boot-starter-parent`) is reported as its own finding:
     "platform upgrade — many managed versions changed", with the parent's own
     release notes analyzed.
   - npm: `package.json` direct deps plus `package-lock.json` resolved versions,
     which gives exact transitive changes for free. To limit cost, analyze release
     notes for direct deps fully; for transitive changes only report major-version
     jumps.
   - New dependencies added are reported (lower severity unless network-, IO- or
     concurrency-related).
   - **Dependency replacement** (the incident's case): a dependency removed and
     another added in the same commit or ticket, or call sites moving from one
     library's API to another's, is treated as a swap. The agent builds a
     "behavior profile" of each side: for the **new external library**, from its
     README, docs and default config at that version; for the **removed in-house
     library**, from its source in Bitbucket, or failing that from how our code
     used it. It then compares connection model (polling, streaming, gRPC,
     websockets), connection count and keep-alive, ports and endpoints, retries,
     caching and failure behavior. A difference in connection model or connection
     count is **high** and routed to gateway/SRE owners.
   - **Reviewer summary:** for every new, upgraded or replaced external library,
     the email includes a short summary of its release notes (upgrades) or
     documented runtime defaults (new or replacement libraries), with links. This
     goes in even when no finding is raised, as context for the lead.
2. **Fetch release notes for every version between old and new:**
   - Find the source repo from npm registry metadata (`repository`) or the Maven
     POM's `<scm>` / `<url>`.
   - Sources, in order: GitHub Releases API → `CHANGELOG.md` / `CHANGES.md` /
     `RELEASE_NOTES.md` in the source repo → a configurable per-library override URL.
   - Cache release notes by library@version permanently, because they don't change.
3. **LLM judgment:** given the notes plus how our code uses the library (grep the
   repo at that commit for imports and config keys of the library), identify changes
   to **defaults or runtime behavior**: connection model or protocol (polling,
   streaming, websockets, keep-alive), connection-pool or thread sizes, retries,
   timeouts, backoff, caching, resource usage, TLS, serialization, removed or
   renamed config. Raise severity if our code relies on defaults that changed.
4. **Unknown is not safe:** if no notes are found for a version jump, emit a
   finding "behavior changes unknown — release notes unavailable", medium for a
   major bump and low otherwise. Here recall beats precision, because this is the
   class of change that caused the outage.
5. **Evidence:** every dependency finding quotes the release-note line and links
   the source.

### 6.5 Code risk and scope analysis
Send the LLM the ticket context (§6.3) plus the diff, with these rules:
- Exclude generated or vendored files and lockfiles (lockfiles are summarized by
  §6.4). Configurable globs.
- Prioritize infra and runtime-config files: `application*.yml|properties`,
  Envoy / gateway / ingress config, Helm / K8s manifests, Dockerfiles,
  `environment*.ts`, `proxy.conf.json`.
- Large diffs: chunk per file group within a token budget; if over budget, analyze
  the highest-priority files and record "partially analyzed" in the finding.

Ask the LLM for:
- **Risky change** findings: a new or changed connection pattern, protocol, client
  config, retry or timeout, concurrency, schema or migration, auth or security
  config, new external call, or feature-flag defaults.
- **Out-of-scope** findings: changes not explained by the ticket, ACs or Epic.
  Neighboring cleanup is fine; formatting-only and test-only changes are never
  flagged as out of scope.

### 6.6 Finding model
Each finding has: `id`, `repo`, `commit`, `author`, `jira_keys`, `type`
(`dependency_behavior_change` | `risky_change` | `out_of_scope` | `ticket_gap`),
`category` (e.g. `connection_model`, `timeouts`, `missing_ac`), `severity`
(`high` | `medium` | `low` | `info`), `title`, `explanation`, `evidence`
(file:line and/or a release-note quote + URL), `suggested_reviewers`, `created_at`.
The LLM returns findings as structured JSON validated against this schema; invalid
output is retried once, then the analysis is recorded as an error.

### 6.7 Notifications
- **Per push** (author + lead): one email per push that has at least one finding of
  severity ≥ `low` (`info` is digest-only). It lists the flagged commits and their
  findings, high first, with evidence and links to the commit and ticket. Clean
  pushes send nothing.
- **Author address:** from the Bitbucket commit's author email, mapped to a user
  where possible.
- **Lead routing** from `routing.yaml`:
  ```yaml
  repos:
    payments-service:
      leads: [lead-a@corp]
      paths:
        "src/main/java/**/flags/**": [flags-owner@corp]
  categories:            # risk category → extra owners
    connection_model: [gateway-owner@corp, platform-sre@corp]
    timeouts:         [platform-sre@corp]
  default_leads: [eng-managers@corp]
  ```
- **PMO daily digest** at a configured time: all findings from the last 24h grouped
  by repo → ticket, high severity first; ticket-gap statistics (commits without a
  key, tickets with missing or vague ACs); count of analysis errors.
- Tone: neutral, "for review"; each email has a short "why this was flagged" line.

### 6.8 Configuration and secrets
All configuration comes from environment variables (see §5.1):
- secrets: Bitbucket token, webhook secret, JIRA credentials, LLM API key, GitHub
  token, Nexus credentials, SMTP credentials, rating-link secret
- settings: branches, JIRA project keys and AC field mapping, file globs, model and
  token budget, timezone, retry, lease and timeout values
- paths to the two mounted data files: routing, and dependency hints

The digest time is set by the platform scheduler that runs the `digest` process.

### 6.9 LLM interface
`LLMClient.analyze(prompt, schema) -> dict`, with a Claude implementation for the
POC and room for a local implementation (e.g. an OpenAI-compatible local server) after.
Prompts live in versioned template files. Log token usage per analysis for cost
tracking.

## 7. POC success criteria
1. **Incident replay:** run the agent on the actual commit(s) that replaced the
   in-house feature-flag util with the flagd library. It must produce a **high**
   `dependency_behavior_change` finding saying the connection model changed from
   polling to streaming, citing the flagd docs, and route it to the gateway owner.
2. **Backtest:** run it on about 200 recent commits across 2–3 repos (Java +
   Angular). Leads rate each finding useful or noise. Target ≥ 70% useful for
   high/medium findings.
3. End-to-end on one live repo for two weeks: per-push emails and the PMO digest
   arrive, with no missed pushes.

A CLI (`analyze --repo X --commit SHA`) supports criteria 1 and 2 without webhooks.

## 8. Left to the builder
- Project layout, libraries and test strategy beyond what's stated.
- Exact prompt wording and chunking strategy.
- Deployment form (container or VM) within the internal network.
- Retry, backoff and cache TTL values.
