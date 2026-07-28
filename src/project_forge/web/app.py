"""FastAPI application for Project Forge dashboard."""

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from project_forge import __version__
from project_forge.config import settings
from project_forge.storage.db import Database
from project_forge.web.auth import BearerTokenMiddleware

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

db = Database(settings.db_path)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
# Version SSOT (#82): the footer renders {{ forge_version }} from
# __version__ so template text can never drift from the package version.
templates.env.globals["forge_version"] = __version__

# Ephemeral dashboard token — fresh on each MACHINE BOOT but stable across
# process restarts and uvicorn --reload re-imports within a boot. Three
# layers of persistence make sure open browser tabs don't 401 unless the
# host actually rebooted:
#   1. Process env var FORGE_DASHBOARD_TOKEN_RUNTIME — survives module reload
#   2. File at /tmp/forge-dashboard-token — survives process restart
#   3. Both reset on machine reboot (/tmp is tmpfs)
import os  # noqa: E402
import secrets  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_DASHBOARD_TOKEN_ENV = "FORGE_DASHBOARD_TOKEN_RUNTIME"  # noqa: S105
_DASHBOARD_TOKEN_FILE = Path(tempfile.gettempdir()) / "forge-dashboard-token"  # noqa: S105


def _load_dashboard_token() -> str:
    # Layer 1: env var (set by a previous import in this same process)
    env_val = os.environ.get(_DASHBOARD_TOKEN_ENV)
    if env_val:
        return env_val
    # Layer 2: tmpfs file (set by a previous process boot)
    try:
        if _DASHBOARD_TOKEN_FILE.exists():
            disk_val = _DASHBOARD_TOKEN_FILE.read_text().strip()
            if disk_val:
                return disk_val
    except OSError:
        pass
    # Layer 3: fresh
    return secrets.token_urlsafe(32)


_dashboard_token = _load_dashboard_token()
os.environ[_DASHBOARD_TOKEN_ENV] = _dashboard_token
try:
    _DASHBOARD_TOKEN_FILE.write_text(_dashboard_token)
    _DASHBOARD_TOKEN_FILE.chmod(0o600)
except OSError as _exc:
    logger.warning("Could not persist dashboard token to %s: %s", _DASHBOARD_TOKEN_FILE, _exc)
templates.env.globals["dashboard_token"] = _dashboard_token


_CSP_SKIP_PATHS = ("/docs", "/redoc", "/openapi.json")

# Client-supplied request IDs are untrusted input reflected into a response
# header, so restrict them to a short, safe alphabet. Anything else (missing,
# too long, control characters, non-ASCII) is replaced with a fresh ID.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def sanitize_request_id(value: str | None) -> str:
    """Return `value` if it is a safe request ID, else a freshly generated one."""
    if value is not None and _REQUEST_ID_RE.match(value):
        return value
    return uuid.uuid4().hex[:16]


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add a unique X-Request-ID to every response for correlation."""

    async def dispatch(self, request: Request, call_next):
        request_id = sanitize_request_id(request.headers.get("x-request-id"))
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        # Skip CSP on FastAPI-generated docs pages (they use inline scripts)
        if not request.url.path.startswith(_CSP_SKIP_PATHS):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "font-src 'self'"
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    logger.info("Database connected: %s", settings.db_path)

    # v0.17 — load any persisted Scoreboard auto-tune nudges so the live
    # scorers reflect them immediately (no-op when the table is empty).
    from project_forge.engine.scoreboard import load_nudges

    await load_nudges(db)

    # In-process scheduler — owns the cadences that used to live in
    # `/etc/systemd/system/project-forge-*.timer` units. Those are
    # unreachable from the sandboxed runtime, so the FastAPI lifespan
    # carries them and uvicorn --reload is the deploy path.
    from project_forge.web.lifespan_scheduler import default_cadences, start_scheduler

    cadences = default_cadences()
    scheduler_task = start_scheduler(db, cadences=cadences)
    logger.info(
        "In-process scheduler started with %d cadences: %s",
        len(cadences),
        ", ".join(c.name for c in cadences),
    )

    try:
        yield
    finally:
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        await db.close()
        logger.info("Database closed")


app = FastAPI(
    title="Project Forge",
    description="Autonomous IT project think-tank engine",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(CSPMiddleware)
app.add_middleware(BearerTokenMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Import and include routes
from project_forge.web.routes import router  # noqa: E402

app.include_router(router)


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Full detail goes to the server log only. The response body stays a fixed
    # generic string so unhandled errors can't leak internals (SQLite column/table
    # names, filesystem paths, library messages) to whoever triggered the 500 —
    # including unauthenticated GET callers, which BearerTokenMiddleware lets through.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


def create_app(db_path=None):
    """Create a test-friendly app instance with an isolated database."""
    from project_forge.storage.db import Database as DB

    test_db = DB(db_path or settings.db_path)

    @asynccontextmanager
    async def test_lifespan(application: FastAPI):
        await test_db.connect()
        # Swap the module-level db reference so routes use the test DB
        import project_forge.web.app as app_mod

        old_db = app_mod.db
        app_mod.db = test_db
        import project_forge.web.routes as routes_mod

        old_routes_db = routes_mod.db
        routes_mod.db = test_db
        yield
        await test_db.close()
        app_mod.db = old_db
        routes_mod.db = old_routes_db

    test_app = FastAPI(lifespan=test_lifespan)
    test_app.add_middleware(CSPMiddleware)
    from project_forge.web.routes import router as r

    test_app.include_router(r)
    return test_app


def run():
    """Entry point for forge-serve command."""
    import uvicorn

    uvicorn.run(
        "project_forge.web.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
