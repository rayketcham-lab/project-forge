"""Rate-limit coverage on the LLM-backed and scaffold endpoints — fix #76.

ingest_url, ingest_text, and scaffold_idea were uncapped. Each invokes
an LLM (or scaffolds a real repo), so they're far more expensive than
the other rate-limited paths (/approve, /promote, /report). The fix
adds `_check_rate_limit(f"ingest:{ip}")` / `scaffold:{ip}` to the
three handlers; these tests assert the 429 once the limit is hit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db
from project_forge.web.routes import _rate_limit_store


@pytest_asyncio.fixture(autouse=True)
def _clear_rate_limit():
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_routes_rl.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


class TestIngestRateLimit:
    @pytest.mark.asyncio
    async def test_ingest_url_429_after_burst(self, client):
        """Five rapid POSTs to /api/ideas/from-url should trip the limiter."""
        # Stub the heavy generation path so we're measuring the gate, not
        # the LLM.
        from project_forge.engine.dedup import filter_and_save  # noqa: F401
        from project_forge.models import Idea, IdeaCategory

        stub_idea = Idea(
            name="x",
            tagline="t",
            description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40,
            feasibility_score=0.6,
            mvp_scope="mvp" * 5,
            tech_stack=["python"],
        )

        async def _fake_ingest(_req):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with (
            patch(
                "project_forge.web.routes.ingest_idea_from_url",
                new=_fake_ingest,
            ),
            patch(
                "project_forge.web.routes.filter_and_save",
                new=_fake_save,
            ),
        ):
            statuses = []
            for _ in range(8):
                resp = await client.post(
                    "/api/ideas/from-url",
                    json={"url": "https://example.com/x", "category": ""},
                )
                statuses.append(resp.status_code)
        # The first call works (200), subsequent calls trip 429 once the
        # window's quota is exhausted.
        assert 429 in statuses, f"never rate-limited; statuses={statuses}"
        assert statuses[0] == 200, f"first call should pass; got {statuses[0]}"

    @pytest.mark.asyncio
    async def test_ingest_text_429_after_burst(self, client):
        from project_forge.models import Idea, IdeaCategory

        stub_idea = Idea(
            name="t",
            tagline="t",
            description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40,
            feasibility_score=0.6,
            mvp_scope="mvp" * 5,
            tech_stack=["python"],
        )

        async def _fake_text(text, category_hint=None):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with (
            patch(
                "project_forge.web.routes.generate_idea_from_text",
                new=_fake_text,
            ),
            patch(
                "project_forge.web.routes.filter_and_save",
                new=_fake_save,
            ),
        ):
            statuses = []
            for _ in range(8):
                resp = await client.post(
                    "/api/ideas/from-text",
                    json={"text": "a fragment", "category": ""},
                )
                statuses.append(resp.status_code)
        assert 429 in statuses
        assert statuses[0] == 200


class TestRateLimitEviction:
    """a2923634 — the store must not grow unbounded; fully-expired keys are
    evicted so a long-running process doesn't leak an entry per client/action."""

    def test_prune_drops_expired_keeps_fresh(self):
        import time

        from project_forge.web.routes import (
            _RATE_LIMIT_WINDOW,
            _prune_rate_limit_store,
            _rate_limit_store,
        )

        now = time.monotonic()
        _rate_limit_store["stale:1"] = [now - _RATE_LIMIT_WINDOW - 5]
        _rate_limit_store["stale:2"] = [now - _RATE_LIMIT_WINDOW - 1, now - _RATE_LIMIT_WINDOW - 2]
        _rate_limit_store["fresh:1"] = [now - 1]

        _prune_rate_limit_store(now)

        assert "stale:1" not in _rate_limit_store
        assert "stale:2" not in _rate_limit_store
        assert "fresh:1" in _rate_limit_store

    def test_check_rate_limit_evicts_once_store_is_large(self):
        import time

        from project_forge.web import routes

        now = time.monotonic()
        for i in range(routes._RATE_LIMIT_PRUNE_THRESHOLD + 50):
            routes._rate_limit_store[f"stale:{i}"] = [now - routes._RATE_LIMIT_WINDOW - 10]
        before = len(routes._rate_limit_store)

        routes._check_rate_limit("liveclient:probe")

        after = len(routes._rate_limit_store)
        assert after < before, "expired keys should have been evicted"
        assert "liveclient:probe" in routes._rate_limit_store, "the live key must remain"

    def test_active_limiting_still_enforced(self):
        """Eviction must not weaken the limiter — a burst still trips 429."""
        import pytest as _pytest
        from fastapi import HTTPException

        from project_forge.web.routes import _RATE_LIMIT_MAX, _check_rate_limit

        for _ in range(_RATE_LIMIT_MAX):
            _check_rate_limit("burst:client")
        with _pytest.raises(HTTPException) as exc:
            _check_rate_limit("burst:client")
        assert exc.value.status_code == 429


class TestRateLimitIsolation:
    @pytest.mark.asyncio
    async def test_unrelated_get_still_passes(self, client):
        """Bursting an ingest endpoint should not lock out a normal stats
        read — the limiter buckets per-action, not per-IP-globally."""
        from project_forge.engine.dedup import filter_and_save  # noqa: F401
        from project_forge.models import Idea, IdeaCategory

        stub_idea = Idea(
            name="x",
            tagline="t",
            description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40,
            feasibility_score=0.6,
            mvp_scope="mvp" * 5,
            tech_stack=["python"],
        )

        async def _fake_ingest(_req):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with (
            patch(
                "project_forge.web.routes.ingest_idea_from_url",
                new=_fake_ingest,
            ),
            patch(
                "project_forge.web.routes.filter_and_save",
                new=_fake_save,
            ),
        ):
            for _ in range(8):
                await client.post(
                    "/api/ideas/from-url",
                    json={"url": "https://example.com/x", "category": ""},
                )
        resp = await client.get("/api/stats")
        assert resp.status_code == 200, "stats GET should be unaffected"
