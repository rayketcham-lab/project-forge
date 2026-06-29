"""Shared test fixtures."""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.storage.db import Database


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_sessionfinish(session, exitstatus):
    """Let pytest finish reporting, then force a clean exit.

    The session-scoped asyncio loop + aiosqlite worker threads leave a
    non-daemon thread that hangs interpreter teardown on this host (and the
    self-hosted CI runner that shares it). A `wrapper` hook lets every inner
    sessionfinish run FIRST — so the terminal summary, the coverage report,
    and the `--cov-fail-under` gate all print and finalize `session.exitstatus`
    — then we flush and `os._exit` with that final status, skipping the hang.
    Unlike the old `trylast` version, this no longer swallows the summary, so
    CI failures are fully visible (legit), not silently truncated.
    """
    result = yield
    # Only force-exit after a REAL test run. Collection-only (CI's test-count
    # metric runs `pytest --collect-only`) never spins up the async loop, so
    # there's nothing to hang on — let it exit normally with full output.
    if not session.config.getoption("collectonly", False):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(session.exitstatus))
    return result


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


@pytest.fixture(autouse=True)
def _isolate_module_caches():
    """Clear runtime module-level caches around every test.

    These globals accumulate state during a run and were NOT reset, so a
    test's result could depend on what ran before it (order-dependent =
    non-reciprocal CI). Clearing them on both sides of every test makes the
    suite deterministic regardless of execution order or which subset runs:
      - verticals._INFER_CACHE  — vertical-inference memoization
      - scoreboard._NUDGE_CACHE — learned auto-tune nudges (would silently
        shift fundability/ambition/snipe heuristic scores in later tests)
    """
    from project_forge.engine.scoreboard import _NUDGE_CACHE
    from project_forge.engine.verticals import _INFER_CACHE

    _INFER_CACHE.clear()
    _NUDGE_CACHE.clear()
    yield
    _INFER_CACHE.clear()
    _NUDGE_CACHE.clear()


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
