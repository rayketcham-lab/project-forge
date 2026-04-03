"""Shared test fixtures."""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest.fixture(autouse=True)
def _isolate_test_env():
    """Reset rate limit store and clear api_token between tests.

    The api_token reset prevents FORGE_API_TOKEN from .env bleeding into tests
    that don't send auth headers, which caused 44 spurious 401 failures.
    """
    from project_forge.config import settings

    original_token = settings.api_token
    settings.api_token = ""

    try:
        from project_forge.web.routes import _rate_limit_store

        _rate_limit_store.clear()
    except ImportError:
        pass

    yield

    settings.api_token = original_token

    try:
        from project_forge.web.routes import _rate_limit_store

        _rate_limit_store.clear()
    except ImportError:
        pass


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()
