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
            name="x", tagline="t", description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40, feasibility_score=0.6,
            mvp_scope="mvp" * 5, tech_stack=["python"],
        )

        async def _fake_ingest(_req):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new=_fake_ingest,
        ), patch(
            "project_forge.web.routes.filter_and_save",
            new=_fake_save,
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
            name="t", tagline="t", description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40, feasibility_score=0.6,
            mvp_scope="mvp" * 5, tech_stack=["python"],
        )

        async def _fake_text(text, category_hint=None):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with patch(
            "project_forge.web.routes.generate_idea_from_text",
            new=_fake_text,
        ), patch(
            "project_forge.web.routes.filter_and_save",
            new=_fake_save,
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


class TestRateLimitIsolation:
    @pytest.mark.asyncio
    async def test_unrelated_get_still_passes(self, client):
        """Bursting an ingest endpoint should not lock out a normal stats
        read — the limiter buckets per-action, not per-IP-globally."""
        from project_forge.engine.dedup import filter_and_save  # noqa: F401
        from project_forge.models import Idea, IdeaCategory

        stub_idea = Idea(
            name="x", tagline="t", description="d" * 80,
            category=IdeaCategory.OBSERVABILITY,
            market_analysis="m" * 40, feasibility_score=0.6,
            mvp_scope="mvp" * 5, tech_stack=["python"],
        )

        async def _fake_ingest(_req):
            return stub_idea

        async def _fake_save(_idea, _db):
            return _idea, True, None

        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new=_fake_ingest,
        ), patch(
            "project_forge.web.routes.filter_and_save",
            new=_fake_save,
        ):
            for _ in range(8):
                await client.post(
                    "/api/ideas/from-url",
                    json={"url": "https://example.com/x", "category": ""},
                )
        resp = await client.get("/api/stats")
        assert resp.status_code == 200, "stats GET should be unaffected"
