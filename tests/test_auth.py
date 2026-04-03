"""Tests for bearer token authentication on POST endpoints."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_idea(**overrides) -> Idea:
    defaults = dict(
        name="Auth Test Idea",
        tagline="Auth test tagline",
        description="Auth test description.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Market.",
        feasibility_score=0.7,
        mvp_scope="Build it.",
        tech_stack=["python"],
    )
    defaults.update(overrides)
    return Idea(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    """Client with NO token set — backward compat baseline."""
    db.db_path = tmp_path / "test_auth.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


@pytest_asyncio.fixture
async def authed_client(tmp_path, monkeypatch):
    """Client whose app has FORGE_API_TOKEN='test-secret' configured."""
    import project_forge.config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "api_token", "test-secret")

    db.db_path = tmp_path / "test_auth_token.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ---------------------------------------------------------------------------
# Tests — token configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_without_token_returns_401_when_configured(authed_client):
    """POST with no Authorization header must return 401 when token is set."""
    resp = await authed_client.post("/ideas/someid/approve")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_with_wrong_token_returns_401(authed_client):
    """POST with the wrong token must return 401."""
    resp = await authed_client.post(
        "/ideas/someid/approve",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_with_correct_token_returns_ok(authed_client):
    """POST with the correct Bearer token must pass auth (and return 200 or 404 — not 401)."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await authed_client.post(
        f"/ideas/{idea.id}/approve",
        headers={"Authorization": "Bearer test-secret"},
    )
    # 401 means auth failed; anything else (200, 404…) means auth passed
    assert resp.status_code != 401
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_requests_dont_require_auth(authed_client):
    """GET endpoints must be accessible without a token even when auth is enabled."""
    for path in ("/", "/health", "/api/stats"):
        resp = await authed_client.get(path)
        assert resp.status_code != 401, f"GET {path} should not require auth, got 401"


@pytest.mark.asyncio
async def test_promote_requires_auth(authed_client):
    """POST /api/thinktank/{id}/promote must return 401 without a valid token."""
    resp = await authed_client.post("/api/thinktank/someid/promote")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_scaffold_requires_auth(authed_client):
    """POST /ideas/{id}/scaffold must return 401 without a valid token."""
    resp = await authed_client.post("/ideas/someid/scaffold")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — no token configured (backward compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_auth_required_when_token_not_set(client):
    """When FORGE_API_TOKEN is empty, POST endpoints work without any auth header."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await client.post(f"/ideas/{idea.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


# ---------------------------------------------------------------------------
# Tests — API token injection into templates (issue #17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_token_injected_in_meta_tag_when_set(authed_client):
    """When FORGE_API_TOKEN is set, pages must include a <meta name='api-token'> tag."""
    resp = await authed_client.get("/")
    assert resp.status_code == 200
    assert 'name="api-token"' in resp.text
    assert 'content="test-secret"' in resp.text


@pytest.mark.asyncio
async def test_api_token_meta_tag_absent_when_not_set(client):
    """When FORGE_API_TOKEN is empty, no api-token meta tag should be rendered."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'name="api-token"' not in resp.text


@pytest.mark.asyncio
async def test_idea_detail_includes_api_token_meta(authed_client):
    """Idea detail page must include the api-token meta tag for approve/reject to work."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await authed_client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert 'name="api-token"' in resp.text
