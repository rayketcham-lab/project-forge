"""Bearer token authentication middleware for Project Forge."""

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from project_forge.config import settings

_SKIP_METHODS = {"GET", "HEAD", "OPTIONS"}
_LOOPBACK = {"127.0.0.1", "::1"}


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Require a valid Bearer token on all non-read HTTP methods.

    Accepts either the configured ``api_token`` (for external API clients) or
    the ephemeral ``dashboard_token`` (injected into HTML pages for browser JS).

    When no ``api_token`` is configured this used to skip auth entirely, for
    every caller. Since ``host`` defaults to ``0.0.0.0``, that published every
    write route — delete, promote, mechanic-run, admin reload — to the whole
    network on a fresh clone. Now an empty token only exempts *loopback*:
    remote callers must still present the dashboard token, which the browser
    already sends on every mutating fetch, so nothing about the UI changes.

    Two limits worth knowing. Reads stay open by design, and the dashboard
    token is embedded in the HTML they serve, so anyone who can fetch a page
    can lift a write credential — set ``FORGE_API_TOKEN`` and keep the port
    off untrusted networks if that matters. And behind a same-host reverse
    proxy every request looks like loopback, which collapses the exemption
    back to "open"; configure the proxy to pass through a real token.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Safe methods never require auth.
        if request.method in _SKIP_METHODS:
            return await call_next(request)

        is_loopback = bool(request.client) and request.client.host in _LOOPBACK

        # Admin reload is localhost-only; no Bearer token required.
        if request.url.path == "/api/admin/reload" and is_loopback:
            return await call_next(request)

        # Validate Authorization header using constant-time comparison.
        auth_header = request.headers.get("Authorization", "")
        if settings.api_token and hmac.compare_digest(auth_header, f"Bearer {settings.api_token}"):
            return await call_next(request)

        # Accept the ephemeral dashboard token (generated per server start).
        from project_forge.web.app import _dashboard_token

        if _dashboard_token and hmac.compare_digest(auth_header, f"Bearer {_dashboard_token}"):
            return await call_next(request)

        # No token configured: local callers (CLI, scripts, tests) stay
        # unauthenticated. Everyone arriving over the network does not.
        if not settings.api_token and is_loopback:
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Bearer"},
        )
