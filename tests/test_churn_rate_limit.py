"""Rate-limit coverage on /api/churn — the most expensive endpoint.

api_churn fires one LLM generation call plus one scoring call (and, on
lab=snipe, live HTTP calls for incumbent intel) per request, yet it was the
only write path without `_check_rate_limit`. A spammed Churn Now button could
burn API credit unchecked. The fix adds `_check_rate_limit(f"churn:{ip}")`
before the LLM dispatch; these tests pin the 429 and prove the gate runs
*before* any generation happens.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db
from project_forge.web.routes import _RATE_LIMIT_MAX, _rate_limit_store


@pytest_asyncio.fixture(autouse=True)
def _clear_rate_limit():
    _rate_limit_store.clear()
    yield
    _rate_limit_store.clear()


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_churn_rl.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


class TestChurnRateLimit:
    @pytest.mark.asyncio
    async def test_sixth_churn_within_window_returns_429(self, client):
        """The limit is 5 per window; the 6th churn must be rejected."""
        calls: list[tuple] = []

        async def _fake_generate(_db, category, mode="novel"):
            calls.append((category, mode))
            return None  # short-circuits before scoring — no LLM cost

        with patch(
            "project_forge.engine.llm_generator.generate_idea_llm",
            new=_fake_generate,
        ):
            statuses = []
            for _ in range(_RATE_LIMIT_MAX + 1):
                resp = await client.post("/api/churn", json={"lab": "money"})
                statuses.append(resp.status_code)

        assert statuses[:_RATE_LIMIT_MAX] == [200] * _RATE_LIMIT_MAX, (
            f"first {_RATE_LIMIT_MAX} churns should pass; got {statuses}"
        )
        assert statuses[-1] == 429, f"6th churn should be rate-limited; got {statuses}"
        # The gate runs before dispatch, so the rejected call cost nothing.
        assert len(calls) == _RATE_LIMIT_MAX, f"generator ran {len(calls)} times, expected {_RATE_LIMIT_MAX}"

    @pytest.mark.asyncio
    async def test_snipe_churn_also_limited(self, client):
        """lab=snipe takes its own generation path — it must be capped too."""
        calls: list[str] = []

        async def _fake_snipe(_db, category):
            calls.append("snipe")
            return None

        with patch(
            "project_forge.engine.llm_generator.generate_snipe_llm",
            new=_fake_snipe,
        ):
            statuses = []
            for _ in range(_RATE_LIMIT_MAX + 1):
                resp = await client.post("/api/churn", json={"lab": "snipe"})
                statuses.append(resp.status_code)

        assert statuses[-1] == 429, f"6th snipe churn should be rate-limited; got {statuses}"
        assert len(calls) == _RATE_LIMIT_MAX

    @pytest.mark.asyncio
    async def test_churn_bucket_is_independent_of_other_actions(self, client):
        """Bursting churn must not lock out an unrelated read."""

        async def _fake_generate(_db, category, mode="novel"):
            return None

        with patch(
            "project_forge.engine.llm_generator.generate_idea_llm",
            new=_fake_generate,
        ):
            for _ in range(_RATE_LIMIT_MAX + 3):
                await client.post("/api/churn", json={"lab": "money"})

        resp = await client.get("/api/stats")
        assert resp.status_code == 200, "stats GET should be unaffected"
