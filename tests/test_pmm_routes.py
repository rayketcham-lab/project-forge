"""Tests for PMM-style dashboard routes and new API endpoints."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_pmm.db"
    await db.connect()
    # Seed test ideas
    for i, cat in enumerate(IdeaCategory):
        idea = Idea(
            name=f"Test Idea {i}",
            tagline=f"Tagline {i}",
            description=f"Description for idea {i}",
            category=cat,
            market_analysis=f"Market {i}",
            feasibility_score=0.5 + (i % 5) * 0.1,
            mvp_scope=f"MVP {i}",
            tech_stack=["python", "fastapi"],
        )
        await db.save_idea(idea)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


@pytest.mark.asyncio
async def test_api_top_ideas(client):
    resp = await client.get("/api/top-ideas")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) <= 10
    # Should be sorted by feasibility_score descending
    scores = [d["feasibility_score"] for d in data]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_api_categories(client):
    resp = await client.get("/api/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 13  # All categories
    for cat in data:
        assert "name" in cat
        assert "count" in cat
        assert "avg_score" in cat


@pytest.mark.asyncio
async def test_api_ideas_by_category(client):
    resp = await client.get("/api/ideas?category=pqc-cryptography")
    assert resp.status_code == 200
    data = resp.json()
    assert "ideas" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_api_search(client):
    resp = await client.get("/api/search?q=Test")
    assert resp.status_code == 200
    data = resp.json()
    assert "ideas" in data
    assert len(data["ideas"]) > 0


@pytest.mark.asyncio
async def test_dashboard_has_hero_section(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "hero" in resp.text.lower() or "Project Forge" in resp.text


@pytest.mark.asyncio
async def test_explore_page(client):
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert resp.status_code == 200
