from fastapi import FastAPI

from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.web import rating, webhook


def create_app(
    webhook_secret: str,
    branches: list[str],
    jobs: JobQueue,
    ratings: RatingRepository,
    rating_secret: str,
) -> FastAPI:
    app = FastAPI(title="scope-agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(webhook.build_router(webhook_secret, branches, jobs))
    app.include_router(rating.build_router(ratings, rating_secret))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app
