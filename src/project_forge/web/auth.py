"""Bearer token authentication middleware for Project Forge."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from project_forge.config import settings

_SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a valid Bearer token on all non-read HTTP methods.

    Auth is skipped entirely when ``settings.api_token`` is empty, preserving
    backward compatibility for local / dev environments.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Auth disabled — nothing to check.
        if not settings.api_token:
            return await call_next(request)

        # Safe methods never require auth.
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        # Validate Authorization header.
        auth_header = request.headers.get("Authorization", "")
        if auth_header == f"Bearer {settings.api_token}":
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        )
