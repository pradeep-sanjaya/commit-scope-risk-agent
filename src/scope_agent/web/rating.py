from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

from scope_agent.notify.rating_links import verify_rating_token
from scope_agent.persistence.ratings import RatingRepository

VALUES = ("useful", "noise")


def build_router(ratings: RatingRepository, rating_secret: str) -> APIRouter:
    router = APIRouter()

    @router.get("/rate/{finding_id}")
    def rate(finding_id: str, v: str = "", t: str = "") -> Response:
        if v not in VALUES:
            return Response(status_code=400)
        if not verify_rating_token(rating_secret, finding_id, v, t):
            return Response(status_code=403)
        ratings.save(finding_id, v)
        return HTMLResponse("<p>Thanks, recorded.</p>")

    return router
