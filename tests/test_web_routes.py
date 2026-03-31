"""Tests for FastAPI web routes."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_web.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_dashboard(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Project Forge" in resp.text


@pytest.mark.asyncio
async def test_ideas_empty(client):
    resp = await client.get("/ideas")
    assert resp.status_code == 200
    assert "No ideas found" in resp.text or "0 ideas" in resp.text


@pytest.mark.asyncio
async def test_api_stats(client):
    resp = await client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_ideas" in data
    assert "total_runs" in data


@pytest.mark.asyncio
async def test_idea_detail_not_found(client):
    resp = await client.get("/ideas/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_idea_lifecycle(client):
    idea = Idea(
        name="Web Test Idea",
        tagline="Testing the web",
        description="A test idea for web routes.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Good market.",
        feasibility_score=0.8,
        mvp_scope="Build it.",
        tech_stack=["python"],
    )
    await db.save_idea(idea)

    # View detail
    resp = await client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert "Web Test Idea" in resp.text

    # Approve
    resp = await client.post(f"/ideas/{idea.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"

    # Scaffold - skip in test (creates real GitHub repos)
    # Tested separately with mocked gh CLI

    # List ideas
    resp = await client.get("/ideas")
    assert resp.status_code == 200
    assert "Web Test Idea" in resp.text


@pytest.mark.asyncio
async def test_reject_idea(client):
    idea = Idea(
        name="Reject Me",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.PRIVACY,
        market_analysis="Market",
        feasibility_score=0.3,
        mvp_scope="MVP",
    )
    await db.save_idea(idea)

    resp = await client.post(f"/ideas/{idea.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_projects_page(client):
    resp = await client.get("/projects")
    assert resp.status_code == 200
    assert "Scaffolded Projects" in resp.text


@pytest.mark.asyncio
async def test_csp_headers(client):
    resp = await client.get("/health")
    assert "Content-Security-Policy" in resp.headers
    assert "X-Content-Type-Options" in resp.headers
