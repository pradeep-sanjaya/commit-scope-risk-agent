import json

from fastapi import APIRouter, Request, Response

from scope_agent.adapters.bitbucket import parse_refs_changed, verify_signature
from scope_agent.persistence.jobs import JobQueue


def build_router(webhook_secret: str, branches: list[str], jobs: JobQueue) -> APIRouter:
    router = APIRouter()

    @router.post("/webhook/bitbucket")
    async def bitbucket(request: Request) -> Response:
        body = await request.body()
        if not verify_signature(webhook_secret, body, request.headers.get("X-Hub-Signature")):
            return Response(status_code=401)
        event = request.headers.get("X-Event-Key")
        if event == "diagnostics:ping":
            return Response(status_code=200)
        if event != "repo:refs_changed":
            return Response(status_code=204)
        try:
            payload = json.loads(body)
            push_jobs = parse_refs_changed(payload, branches)
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
