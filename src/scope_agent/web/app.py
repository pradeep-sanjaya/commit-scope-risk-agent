from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from scope_agent.persistence.jobs import JobQueue
from scope_agent.persistence.ratings import RatingRepository
from scope_agent.services.portal_queries import PortalQueries
from scope_agent.web import portal, rating, webhook
from scope_agent.web.auth import require_portal_user

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    webhook_secret: str,
    branches: list[str],
    jobs: JobQueue,
    ratings: RatingRepository,
    rating_secret: str,
    portal_queries: PortalQueries | None = None,
    portal_auth_header: str | None = None,
) -> FastAPI:
    app = FastAPI(title="scope-agent", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(webhook.build_router(webhook_secret, branches, jobs))
    app.include_router(rating.build_router(ratings, rating_secret))
    if portal_queries is not None:
        app.mount("/portal/static", StaticFiles(directory=STATIC_DIR), name="portal-static")
        app.include_router(portal.build_router(portal_queries, require_portal_user(portal_auth_header)))

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app
