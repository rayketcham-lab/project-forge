"""Tests for the /money-bots page + /api/money-bots/top endpoint.

Covers what the auto-promote cadence surfaces to the user: fundability-
ranked listing across the 4 money categories, only ideas with a
non-NULL fundability_score, and a JSON API that returns the same data.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


def _money_idea(name: str, cat: IdeaCategory, score: float | None) -> Idea:
    idea = Idea(
        name=name,
        tagline=f"tag {name}",
        description="d" * 80,
        category=cat,
        market_analysis="m" * 40,
        feasibility_score=0.7,
        mvp_scope="mvp" * 5,
        tech_stack=["python", "fastapi"],
    )
    idea.fundability_score = score
    return idea


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_money_bots.db"
    await db.connect()
    # Seed: 3 money ideas with scores, 1 with NULL score, 1 in a non-money cat.
    await db.save_idea(_money_idea("Money Top", IdeaCategory.AUTOMATION_INCOME, 0.90))
    await db.save_idea(_money_idea("Money Mid", IdeaCategory.CREATOR_TOOLS, 0.65))
    await db.save_idea(_money_idea("Money Low", IdeaCategory.PRODUCTIVITY, 0.40))
    await db.save_idea(_money_idea("Unscored", IdeaCategory.CONSUMER_APP, None))
    await db.save_idea(_money_idea("Sec Tool", IdeaCategory.SECURITY_TOOL, 0.99))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


class TestApiTop:
    @pytest.mark.asyncio
    async def test_returns_only_money_categories_with_scores(self, client):
        resp = await client.get("/api/money-bots/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        names = [d["name"] for d in data]
        # All three scored money ideas — and ONLY those.
        assert set(names) == {"Money Top", "Money Mid", "Money Low"}

    @pytest.mark.asyncio
    async def test_sorted_by_fundability_desc(self, client):
        resp = await client.get("/api/money-bots/top?limit=10")
        scores = [d["fundability_score"] for d in resp.json()]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_limit_param_caps_results(self, client):
        resp = await client.get("/api/money-bots/top?limit=2")
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_payload_shape(self, client):
        resp = await client.get("/api/money-bots/top?limit=1")
        item = resp.json()[0]
        for field in (
            "id", "name", "tagline", "category", "fundability_score",
            "generation_mode", "status", "github_issue_url", "auto_promoted_at",
        ):
            assert field in item


class TestHtmlPage:
    @pytest.mark.asyncio
    async def test_renders_with_money_table(self, client):
        resp = await client.get("/money-bots")
        assert resp.status_code == 200
        html = resp.text
        assert "Money-Bots" in html or "money-bot" in html.lower()
        assert "Money Top" in html
        assert "Money Mid" in html
        # The non-money category and unscored ideas don't surface here.
        assert "Sec Tool" not in html
        assert "Unscored" not in html

    @pytest.mark.asyncio
    async def test_category_filter_narrows_results(self, client):
        resp = await client.get("/money-bots?category=automation-income")
        html = resp.text
        assert "Money Top" in html
        # Other money categories filtered out.
        assert "Money Mid" not in html

    @pytest.mark.asyncio
    async def test_unknown_category_falls_back_to_all(self, client):
        """A bogus category param shouldn't 500; we silently widen to all."""
        resp = await client.get("/money-bots?category=not-real")
        assert resp.status_code == 200
        html = resp.text
        assert "Money Top" in html


class TestDashboardStats:
    @pytest.mark.asyncio
    async def test_money_bot_count_in_stats(self, client):
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "money_bot_count" in data
        assert "auto_promoted_count" in data
        # 4 ideas across money categories (Money Top, Money Mid, Money Low, Unscored).
        assert data["money_bot_count"] == 4
