"""Tests for /explore status filter — regression for issue #51 (empty status param 422)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_explore_filter.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


def _make_idea(name="Test Idea", status="new", **kw):
    defaults = {
        "name": name,
        "tagline": "A test idea",
        "description": "Test description.",
        "category": IdeaCategory.SECURITY_TOOL,
        "market_analysis": "Test market.",
        "feasibility_score": 0.75,
        "mvp_scope": "Test scope.",
        "tech_stack": ["python"],
        "status": status,
    }
    defaults.update(kw)
    return Idea(**defaults)


@pytest.mark.asyncio
async def test_explore_empty_status_param_returns_200(client):
    """?status= (empty string from 'All Statuses' select) must not return 422."""
    resp = await client.get("/explore?status=")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_explore_valid_status_param(client):
    """?status=new should filter ideas by status."""
    idea = _make_idea(name="New Idea", status="new")
    await db.save_idea(idea)
    resp = await client.get("/explore?status=new")
    assert resp.status_code == 200
    assert "New Idea" in resp.text


@pytest.mark.asyncio
async def test_explore_invalid_status_param_returns_422(client):
    """?status=bogus should still return 422 — only empty string is special-cased."""
    resp = await client.get("/explore?status=bogus")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_explore_all_statuses_shows_all(client):
    """No status param should show ideas of all statuses."""
    idea_new = _make_idea(name="New One", status="new")
    idea_approved = _make_idea(name="Approved One", status="approved")
    await db.save_idea(idea_new)
    await db.save_idea(idea_approved)
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert "New One" in resp.text
    assert "Approved One" in resp.text


@pytest.mark.asyncio
async def test_explore_status_select_has_all_statuses_option(client):
    """The status select must have an empty-value 'All Statuses' option."""
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert 'value=""' in resp.text or "value=''" in resp.text
    assert "All Statuses" in resp.text


@pytest.mark.asyncio
async def test_explore_status_select_wired_via_js(client):
    """Status select must use id= so app.js can attach the handler (no inline onchange — CSP blocks it)."""
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert 'id="explore-status-select"' in resp.text
    assert "onchange" not in resp.text.split("filter-status")[1].split("</select>")[0]
