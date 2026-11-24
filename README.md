# scope-agent

Commit scope and risk agent. A Bitbucket Data Center push webhook queues a job. A worker analyzes the
commits with JIRA context and an LLM, then emails neutral, "for review" findings. Nothing blocks a push or
merge, and nothing is written back to Bitbucket or JIRA.

## Process model (twelve-factor)

One image, one entrypoint (`scope-agent`), several process types. All configuration is environment variables
(see below); state lives in the database. Processes are stateless.

| Command | Role |
|---|---|
| `scope-agent web` | Webhook (`/webhook/bitbucket`), rating links, `/healthz`. Binds `0.0.0.0:$PORT`. |
| `scope-agent worker` | Leased job worker. On SIGTERM/SIGINT it finishes the current job, then exits 0. |
| `scope-agent digest` | Sends the digest once and exits. Run it from a scheduler. |
| `scope-agent migrate` | Creates the database schema (one-off). |
| `scope-agent analyze PAY/repo <sha> [--send-email] [--json]` | Analyze one commit on demand. |
| `scope-agent backtest PAY/repo --branch development --limit 200 [--no-send-email]` | Replay recent commits. |
| `scope-agent report` | Useful-rate per type and severity; high+medium against the 70% target. |

A missing or invalid setting exits with status 2 and names the variable. Logs are JSON lines on stdout.

## Run locally

```
cp .env.example .env      # fill in the credentials
docker compose build
docker compose run --rm migrate
docker compose up -d web worker
curl -fsS localhost:8080/healthz
```

Compose also starts Postgres 16 and Mailpit (mail UI on http://localhost:8025). Without Docker:
`uv sync && uv run scope-agent migrate`.

## Data files

Routing, dependency hints and areas are YAML files read from the paths in `SCOPE_ROUTING_FILE`,
`SCOPE_DEPENDENCY_HINTS_FILE` and `SCOPE_AREAS_FILE`. Nothing is baked into the image. Compose mounts
`./deploy` read-only at `/config`, so the defaults point at `/config/*.example.yaml`. In production, mount
your own files (a Kubernetes ConfigMap or volume) and set the three variables to their paths.
Start from `deploy/*.example.yaml`.

## Configuration

Generated from `.env.example`.

| Variable | Default | Description |
|---|---|---|
| `SCOPE_BITBUCKET_URL` | required | Bitbucket Data Center base URL |
| `SCOPE_BITBUCKET_TOKEN` | required | Bitbucket Bearer token |
| `SCOPE_WEBHOOK_SECRET` | required | Shared secret for the X-Hub-Signature webhook HMAC |
| `SCOPE_JIRA_URL` | required | JIRA Cloud base URL |
| `SCOPE_JIRA_EMAIL` | required | JIRA service account email (basic auth) |
| `SCOPE_JIRA_TOKEN` | required | JIRA API token |
| `SCOPE_JIRA_PROJECT_KEYS` | required | Comma-separated JIRA project keys to recognise in commit messages |
| `SCOPE_BRANCHES` | `development,hotfix/*,release/*` | Comma-separated branch patterns to analyze |
| `SCOPE_JIRA_AC_FIELD` | optional | JIRA custom field id holding acceptance criteria (optional) |
| `SCOPE_JIRA_AC_HEADING` | `Acceptance Criteria` | Description heading used to find acceptance criteria when no field is set |
| `SCOPE_JIRA_CACHE_TTL_SECONDS` | `600` | Seconds to cache JIRA ticket lookups |
| `SCOPE_LLM_PROVIDER` | `anthropic` | LLM provider (only "anthropic" is supported) |
| `SCOPE_LLM_API_KEY` | required | LLM API key |
| `SCOPE_LLM_MODEL` | `claude-sonnet-5-5` | LLM model name |
| `SCOPE_LLM_BASE_URL` | optional | LLM API base URL override (optional) |
| `SCOPE_LLM_DAILY_TOKEN_BUDGET` | `2000000` | Daily LLM token budget; jobs defer to the next day when exhausted |
| `SCOPE_LLM_MAX_INPUT_TOKENS` | `60000` | Maximum input tokens per LLM call |
| `SCOPE_LLM_MAX_OUTPUT_TOKENS` | `4096` | Maximum output tokens per LLM call |
| `SCOPE_FILES_EXCLUDE` | `**/package-lock.json,**/*.min.js,**/generated/**,**/dist/**,**/target/**,**/node_modules/**` | Comma-separated globs of files excluded from analysis |
| `SCOPE_FILES_PRIORITY` | `**/application*.yml,**/application*.yaml,**/application*.properties,**/*envoy*.y*ml,**/*gateway*.y*ml,**/helm/**,**/k8s/**,**/Dockerfile,**/environment*.ts,**/proxy.conf.json` | Comma-separated globs of files analyzed first |
| `SCOPE_MAX_COMMITS_PER_PUSH` | `200` | Maximum commits analyzed per push |
| `SCOPE_MAX_CHUNKS_PER_COMMIT` | `3` | Maximum LLM chunks per commit |
| `SCOPE_GITHUB_API_URL` | `https://api.github.com` | GitHub API base URL (release notes) |
| `SCOPE_GITHUB_TOKEN` | optional | GitHub token (optional) |
| `SCOPE_NEXUS_URL` | required | Nexus base URL |
| `SCOPE_NEXUS_USER` | required | Nexus user |
| `SCOPE_NEXUS_PASSWORD` | required | Nexus password |
| `SCOPE_NEXUS_MAVEN_REPO` | `maven-public` | Nexus Maven repository name |
| `SCOPE_NEXUS_NPM_REPO` | `npm-group` | Nexus npm repository name |
| `SCOPE_DOCS_MAX_CHARS` | `40000` | Maximum characters of library docs sent to the LLM |
| `SCOPE_USAGE_CONTEXT_MAX_CHARS` | `20000` | Maximum characters of usage context sent to the LLM |
| `SCOPE_LIBRARY_PROFILE_MAX_CHARS` | `30000` | Maximum characters of a library profile |
| `SCOPE_SMTP_HOST` | `mailpit` | SMTP host (docker-compose runs mailpit; its UI is on port 8025) |
| `SCOPE_SMTP_PORT` | `1025` | SMTP port (mailpit listens on 1025) |
| `SCOPE_SMTP_USER` | optional | SMTP user (optional) |
| `SCOPE_SMTP_PASSWORD` | optional | SMTP password (optional) |
| `SCOPE_SMTP_STARTTLS` | `false` | Use STARTTLS (true/false); mailpit does not offer it |
| `SCOPE_SMTP_FROM` | required | From address for emails |
| `SCOPE_PUBLIC_BASE_URL` | required | Public base URL used in email links |
| `SCOPE_RATING_SECRET` | required | Secret used to sign rating links |
| `SCOPE_REPORT_DEBOUNCE_SECONDS` | `300` | Seconds to wait before generating an epic report after the last change |
| `SCOPE_PORTAL_AUTH_HEADER` | optional | Request header carrying the authenticated portal user (optional) |
| `SCOPE_DATABASE_URL` | required (compose value shown) | Database URL; the value below matches docker-compose |
| `SCOPE_DIGEST_RECIPIENTS` | required | Comma-separated digest recipient emails |
| `SCOPE_ROUTING_FILE` | `/config/routing.example.yaml` | Path to the routing YAML file (docker-compose mounts ./deploy at /config) |
| `SCOPE_DEPENDENCY_HINTS_FILE` | `/config/dependency-hints.example.yaml` | Path to the dependency hints YAML file (docker-compose: /config/...) |
| `SCOPE_AREAS_FILE` | `/config/areas.example.yaml` | Path to the areas YAML file (docker-compose: /config/...) |
| `SCOPE_TIMEZONE` | `UTC` | Timezone for digests and scheduling |
| `SCOPE_HTTP_TIMEOUT_SECONDS` | `30.0` | Outbound HTTP timeout in seconds |
| `SCOPE_JOB_LEASE_SECONDS` | `900` | Job lease duration in seconds |
| `SCOPE_RETRY_BACKOFF_SECONDS` | `30,120,600` | Comma-separated retry backoff delays in seconds |
| `SCOPE_WORKER_POLL_SECONDS` | `2.0` | Worker queue poll interval in seconds |
| `SCOPE_LOG_LEVEL` | `INFO` | Log level |
| `PORT` | `8080` | Web server port (no SCOPE_ prefix) |

## Bitbucket Data Center webhook

1. Create a service account HTTP access token with read access to the repositories and set it as
   `SCOPE_BITBUCKET_TOKEN`.
2. In the repository (or project): Repository settings, Webhooks, Create webhook.
3. URL: `{SCOPE_PUBLIC_BASE_URL}/webhook/bitbucket`. Event: Repository, Push.
4. Secret: the value of `SCOPE_WEBHOOK_SECRET`. Bitbucket signs each delivery as
   `X-Hub-Signature: sha256=<hex>`; unsigned or wrongly signed requests get 401.

Bitbucket's "Test connection" sends an unsigned ping, so it only succeeds when a secret is configured on the
webhook; with no secret it receives 401.

## JIRA and Nexus accounts

- JIRA Cloud: create an API token for a service account and set `SCOPE_JIRA_EMAIL` and `SCOPE_JIRA_TOKEN`
  (REST v3, basic auth). The account needs Browse Projects on the keys in `SCOPE_JIRA_PROJECT_KEYS`.
- Nexus: create a read-only account (read on the Maven and npm repositories) and set `SCOPE_NEXUS_USER` and
  `SCOPE_NEXUS_PASSWORD`. It is used to read POMs and package metadata to find a library's source repository.

## Scheduling the digest

The digest is a run-to-completion process; the platform schedules it, for example weekdays at 18:00:

```
0 18 * * 1-5   scope-agent digest
```

As a Kubernetes CronJob use `schedule: "0 18 * * 1-5"` with the same image, `args: ["digest"]` and the same
environment. The schedule is evaluated in the scheduler's timezone; `SCOPE_TIMEZONE` governs budget days and
deferral.

## Job leases (limitation)

The worker takes a lease of `SCOPE_JOB_LEASE_SECONDS` on a job and does not renew it. Set the lease longer than
the worst-case push analysis time. If a lease expires, another worker reclaims the job; the original worker is
fenced (its completion, retry and failure updates are rejected by attempt number), so it cannot overwrite the
reclaimed job's outcome. The two runs can still overlap in time, so a slow analysis may be done twice.

## Manual incident replay

To confirm the agent catches the motivating incident (a client library replaced by flagd):

1. Add the flagd documentation URL under `library_docs` for
   `dev.openfeature.contrib.providers:flagd` in the dependency hints file (and, if useful, the swap under
   `known_swaps`).
2. Run `scope-agent analyze PAY/<repo> <swap-sha> --json`.
3. Expect a high-severity `connection_model` finding and a "Library changes" summary for flagd.

## Tests

`uv run pytest -v`
