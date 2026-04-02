"""FastAPI application for Project Forge dashboard."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from project_forge.config import settings
from project_forge.storage.db import Database
from project_forge.web.auth import BearerTokenMiddleware

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

db = Database(settings.db_path)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _get_api_token() -> str:
    """Return the configured API token (empty string if unset)."""
    return settings.api_token


templates.env.globals["get_api_token"] = _get_api_token


class CSPMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
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
    yield
    await db.close()
    logger.info("Database closed")


app = FastAPI(
    title="Project Forge",
    description="Autonomous IT project think-tank engine",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(CSPMiddleware)
app.add_middleware(BearerTokenMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Import and include routes
from project_forge.web.routes import router  # noqa: E402

app.include_router(router)


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
