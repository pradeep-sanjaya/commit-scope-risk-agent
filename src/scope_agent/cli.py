"""Process types: web, worker, digest and the admin commands."""

import json
import signal
import threading
from typing import Annotated

import structlog
import typer
import uvicorn

from scope_agent.composition import Container, build_container
from scope_agent.domain.models import PushJob
from scope_agent.logging import configure_logging
from scope_agent.settings import Settings, load_settings
from scope_agent.web.app import create_app

app = typer.Typer(add_completion=False, no_args_is_help=True)
log = structlog.get_logger()

USEFUL_TARGET = 0.70
SYNTHETIC_JOB_ID = 0


def _boot() -> tuple[Settings, Container]:
    settings = load_settings()
    configure_logging(settings.log_level)
    return settings, build_container(settings)


@app.command()
def web() -> None:
    """Serve the webhook, rating links and portal."""
    settings, c = _boot()
    api = create_app(
        webhook_secret=settings.webhook_secret.get_secret_value(),
        branches=settings.branches, jobs=c.jobs, ratings=c.ratings,
        rating_secret=settings.rating_secret.get_secret_value(),
    )
    uvicorn.run(api, host="0.0.0.0", port=settings.port, log_config=None)


@app.command()
def worker() -> None:
    """Process queued jobs until SIGTERM/SIGINT; the current job finishes first."""
    settings, c = _boot()
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker_started")
    c.worker.run(stop, settings.worker_poll_seconds)
    log.info("worker_stopped")


@app.command()
def digest() -> None:
    """Send the digest once and exit (run it from the platform scheduler)."""
    _, c = _boot()
    c.digest.run()


@app.command()
def migrate() -> None:
    """Create the database schema."""
    _, c = _boot()
    c.db.create_all()
    log.info("migrated")


def _analyze_commit(c: Container, repo: str, sha: str, branch: str, send_email: bool):
    parents = c.bitbucket.get_commit(repo, sha).parents
    job = PushJob(repo=repo, branch=branch, from_hash=parents[0] if parents else None,
                  to_hash=sha, pushed_by=None)
    result = c.pipeline.run(SYNTHETIC_JOB_ID, job, force=True)
    if send_email:
        c.notifier.notify(result)
    return result


@app.command()
def analyze(
    repo: str,
    sha: str,
    send_email: Annotated[bool, typer.Option("--send-email/--no-send-email")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Analyze one commit (REPO is PROJ/slug) and print the findings."""
    _, c = _boot()
    result = _analyze_commit(c, repo, sha, "manual", send_email)
    if as_json:
        typer.echo(json.dumps({
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "summaries": {k: [s.model_dump(mode="json") for s in v] for k, v in result.summaries.items()},
        }, indent=2))
        return
    for f in result.findings:
        typer.echo(f"{f.severity.value:<7}{f.category:<20}{f.commit[:10]}  {f.title}")
    for sha_, items in result.summaries.items():
        for s in items:
            typer.echo(f"summary {sha_[:10]} {s.library} ({s.kind}): {s.summary}")
    if not result.findings and not result.summaries:
        typer.echo("no findings")


@app.command()
def backtest(
    repo: str,
    branch: str = "development",
    limit: int = 200,
    send_email: Annotated[bool, typer.Option("--send-email/--no-send-email")] = True,
) -> None:
    """Replay the latest commits of a branch, one synthetic push each."""
    _, c = _boot()
    shas = c.bitbucket.recent_commits(repo, branch, limit)
    total = 0
    for sha in shas:
        result = _analyze_commit(c, repo, sha, branch, send_email)
        total += len(result.findings)
    typer.echo(f"analyzed {len(shas)} commit(s), {total} finding(s)")


@app.command()
def report() -> None:
    """Useful rate per finding type and severity, and overall high+medium against 70%."""
    _, c = _boot()
    rows = c.ratings.report()
    typer.echo(f"{'type':<28}{'severity':<10}{'useful':>7}{'noise':>7}{'rate':>7}")
    for r in rows:
        n = r["useful"] + r["noise"]
        typer.echo(f"{r['type']:<28}{r['severity']:<10}{r['useful']:>7}{r['noise']:>7}"
                   f"{r['useful'] / n:>7.0%}")
    hm = [r for r in rows if r["severity"] in ("high", "medium")]
    useful = sum(r["useful"] for r in hm)
    rated = useful + sum(r["noise"] for r in hm)
    if rated == 0:
        typer.echo("high+medium: no ratings yet")
        return
    rate = useful / rated
    verdict = "meets" if rate >= USEFUL_TARGET else "below"
    typer.echo(f"high+medium useful rate: {rate:.0%} ({useful}/{rated}), {verdict} target {USEFUL_TARGET:.0%}")
