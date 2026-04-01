"""Tests for FastAPI web routes."""

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory, ScaffoldSpec
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


# ---------------------------------------------------------------------------
# Scaffold route tests
# ---------------------------------------------------------------------------

def _make_idea(**overrides) -> Idea:
    """Factory for a minimal valid Idea."""
    defaults = dict(
        name="Test Scaffold Idea",
        tagline="A tagline for scaffolding",
        description="Description of the idea.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Good market.",
        feasibility_score=0.75,
        mvp_scope="Build an MVP.",
        tech_stack=["python"],
    )
    defaults.update(overrides)
    return Idea(**defaults)


def _fake_spec(idea: Idea) -> ScaffoldSpec:
    return ScaffoldSpec(
        idea_id=idea.id,
        repo_name="test-scaffold-idea",
        language="python",
        initial_issues=[{"title": "Init", "body": "Initial issue"}],
    )


@pytest.mark.asyncio
async def test_scaffold_nonexistent_idea_returns_404(client):
    resp = await client.post("/ideas/nonexistent/scaffold")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_scaffold_rejected_idea_returns_400(client):
    idea = _make_idea(status="rejected")
    await db.save_idea(idea)

    resp = await client.post(f"/ideas/{idea.id}/scaffold")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_scaffold_already_scaffolded_returns_400(client):
    idea = _make_idea(status="scaffolded")
    await db.save_idea(idea)

    resp = await client.post(f"/ideas/{idea.id}/scaffold")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_scaffold_idea_happy_path(client):
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    fake_repo_url = "https://github.com/testowner/test-scaffold-idea"

    with (
        patch("project_forge.scaffold.builder.build_scaffold_spec", return_value=_fake_spec(idea)) as mock_build,
        patch("project_forge.scaffold.builder.render_scaffold", return_value=MagicMock()) as mock_render,
        patch("project_forge.scaffold.github.create_repo", return_value=fake_repo_url) as mock_create,
        patch("project_forge.scaffold.github.push_initial_commit") as mock_push,
        patch("project_forge.scaffold.github.create_issue") as mock_issue,
    ):
        resp = await client.post(f"/ideas/{idea.id}/scaffold")

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_url"] == fake_repo_url
    assert body["status"] == "scaffolded"

    # Verify DB was updated
    updated = await db.get_idea(idea.id)
    assert updated is not None
    assert updated.status == "scaffolded"
    assert updated.project_repo_url == fake_repo_url

    # Verify mocks were called
    mock_build.assert_called_once_with(idea)
    mock_render.assert_called_once()
    mock_create.assert_called_once()
    mock_push.assert_called_once()
    mock_issue.assert_called_once()


@pytest.mark.asyncio
async def test_scaffold_github_error_returns_500(client):
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    with (
        patch("project_forge.scaffold.builder.build_scaffold_spec", return_value=_fake_spec(idea)),
        patch("project_forge.scaffold.builder.render_scaffold", return_value=MagicMock()),
        patch("project_forge.scaffold.github.create_repo", side_effect=RuntimeError("GitHub API failure")),
    ):
        resp = await client.post(f"/ideas/{idea.id}/scaffold")

    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_explore_invalid_category_returns_error(client):
    """Passing an unknown category value must return 400 or 422, not a 500 crash."""
    resp = await client.get("/explore?category=not-a-real-category")
    assert resp.status_code in (400, 422), (
        f"Expected 400 or 422 for unknown category, got {resp.status_code}"
    )
