from collections.abc import Callable

from fastapi import HTTPException, Request


def require_portal_user(header_name: str | None) -> Callable[[Request], str | None]:
    """FastAPI dependency: the reverse proxy authenticates; we only insist its user header is present."""

    def dependency(request: Request) -> str | None:
        if not header_name:
            return None
        user = request.headers.get(header_name, "").strip()
        if not user:
            raise HTTPException(status_code=401, detail="authentication required")
        return user

    return dependency
