import json
from collections.abc import Callable

from fastapi import APIRouter, Request, Response

from scope_agent.adapters.bitbucket import parse_refs_changed
from scope_agent.adapters.github_source import parse_push
from scope_agent.adapters.source import SourceProvider, verify_signature
from scope_agent.domain.models import PushJob
from scope_agent.persistence.jobs import JobQueue

# provider -> (path, event header, push event, ping event, signature header, payload parser)
_PROVIDERS: dict[str, tuple[str, str, str, str, str, Callable[[dict, list[str]], list[PushJob]]]] = {
    "bitbucket": ("/webhook/bitbucket", "X-Event-Key", "repo:refs_changed", "diagnostics:ping",
                  "X-Hub-Signature", parse_refs_changed),
    "github": ("/webhook/github", "X-GitHub-Event", "push", "ping", "X-Hub-Signature-256", parse_push),
}


def build_router(webhook_secret: str, branches: list[str], jobs: JobQueue,
                 provider: SourceProvider = "bitbucket") -> APIRouter:
    """Only the configured provider's webhook is served."""
    router = APIRouter()
    path, event_header, push_event, ping_event, signature_header, parse = _PROVIDERS[provider]

    @router.post(path)
    async def webhook(request: Request) -> Response:
        body = await request.body()
        if not verify_signature(webhook_secret, body, request.headers.get(signature_header)):
            return Response(status_code=401)
        event = request.headers.get(event_header)
        if event == ping_event:
            return Response(status_code=200)
        if event != push_event:
            return Response(status_code=204)
        try:
            push_jobs = parse(json.loads(body), branches)
        except (ValueError, KeyError, TypeError):
            return Response(status_code=400)
        queued = 0
        for job in push_jobs:
            key = f"push:{job.repo}:{job.branch}:{job.to_hash}"
            if jobs.enqueue("push", job.model_dump(mode="json"), key) is not None:
                queued += 1
        return Response(content=json.dumps({"queued": queued}), status_code=202,
                        media_type="application/json")

    return router
