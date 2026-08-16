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
# Tests — no FORGE_API_TOKEN configured
#
# The default used to be "no token set → skip auth for everyone". Since the
# server also defaults to binding 0.0.0.0, a fresh clone published every write
# route to the whole network. An empty token now exempts loopback only.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def remote_client(tmp_path):
    """Client that arrives from off-host, as a network attacker would."""
    db.db_path = tmp_path / "test_auth_remote.db"
    await db.connect()
    transport = ASGITransport(app=app, client=("203.0.113.7", 44321))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


@pytest.mark.asyncio
async def test_remote_write_rejected_when_no_token_configured(remote_client):
    """The default install must not accept blind writes off the network."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await remote_client.post(f"/ideas/{idea.id}/approve")
    assert resp.status_code == 401

    fresh = await db.get_idea(idea.id)
    assert fresh.status == "new", "unauthenticated remote POST mutated state"


@pytest.mark.asyncio
async def test_remote_write_allowed_with_dashboard_token(remote_client):
    """A remote browser still works: base.html hands app.js the dashboard
    token and it rides along on every mutating fetch."""
    from project_forge.web.app import _dashboard_token

    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await remote_client.post(
        f"/ideas/{idea.id}/approve",
        headers={"Authorization": f"Bearer {_dashboard_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_loopback_write_still_unauthenticated_when_no_token_set(client):
    """Local callers — scripts, cron one-shots, the test suite — are unchanged."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await client.post(f"/ideas/{idea.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_remote_reads_stay_open(remote_client):
    """GET must not start 401-ing — the dashboard is read-open by design."""
    for path in ("/", "/health", "/api/stats"):
        resp = await remote_client.get(path)
        assert resp.status_code != 401, f"GET {path} regressed to 401"


# ---------------------------------------------------------------------------
# Tests — API token injection into templates (issue #17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_token_not_in_html_when_set(authed_client):
    """API token must never appear in HTML (#42 — security fix)."""
    resp = await authed_client.get("/")
    assert resp.status_code == 200
    assert 'name="api-token"' not in resp.text


@pytest.mark.asyncio
async def test_api_token_meta_tag_absent_when_not_set(client):
    """When FORGE_API_TOKEN is empty, no api-token meta tag should be rendered."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'name="api-token"' not in resp.text


@pytest.mark.asyncio
async def test_idea_detail_no_api_token_meta(authed_client):
    """Idea detail page must NOT include the api-token meta tag (#42 — security fix)."""
    idea = _make_idea(status="new")
    await db.save_idea(idea)

    resp = await authed_client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert 'name="api-token"' not in resp.text
