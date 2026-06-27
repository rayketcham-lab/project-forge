"""Tests for quick-action buttons on idea cards in /explore — issue #52."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_card_actions.db"
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
async def test_cards_have_action_buttons(client):
    """Each idea card should render approve/reject/delete action buttons."""
    idea = _make_idea(name="Action Test Idea")
    await db.save_idea(idea)
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert 'data-action="approve"' in resp.text
    assert 'data-action="reject"' in resp.text
    assert 'data-action="delete"' in resp.text


@pytest.mark.asyncio
async def test_cards_have_check_repo_button(client):
    """Each card should have a check-repo action button."""
    idea = _make_idea(name="Check Repo Idea")
    await db.save_idea(idea)
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert 'data-action="check"' in resp.text


@pytest.mark.asyncio
async def test_card_is_div_not_anchor_wrapper(client):
    """Cards must use a `<div class="idea-card" data-idea-id>` so action
    buttons render as valid HTML AND the v0.14c in-window modal handler
    can intercept the click (an <a href> wrapper would force a page
    navigation before the modal opened). The `data-href` attribute was
    removed in v0.14c — the modal opens via the data-idea-id document-
    level click delegate now."""
    idea = _make_idea(name="Div Card Idea")
    await db.save_idea(idea)
    resp = await client.get("/explore")
    assert resp.status_code == 200
    # The card uses div+data-idea-id (the modal entry point).
    assert "data-idea-id=" in resp.text
    # And NOT the old anchor wrapper, which would hijack the click.
    assert '<a href="/ideas/' not in resp.text


@pytest.mark.asyncio
async def test_card_has_idea_id_data_attribute(client):
    """Card must expose data-idea-id so JS can POST actions."""
    idea = _make_idea(name="ID Attr Idea")
    saved = await db.save_idea(idea)
    resp = await client.get("/explore")
    assert resp.status_code == 200
    assert f'data-idea-id="{saved.id}"' in resp.text


@pytest.mark.asyncio
async def test_approved_idea_hides_approve_reject_buttons(client):
    """Approved/rejected ideas should not show approve/reject action buttons."""
    idea = _make_idea(name="Already Approved", status="approved")
    await db.save_idea(idea)
    resp = await client.get("/explore?status=approved")
    assert resp.status_code == 200
    # No approve/reject buttons for already-actioned ideas
    assert 'data-action="approve"' not in resp.text
    assert 'data-action="reject"' not in resp.text


@pytest.mark.asyncio
async def test_approve_endpoint_works(client):
    """POST /ideas/{id}/approve should change status to approved."""
    idea = _make_idea(name="Approve Me")
    saved = await db.save_idea(idea)
    resp = await client.post(f"/ideas/{saved.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_reject_endpoint_works(client):
    """POST /ideas/{id}/reject should change status to rejected."""
    idea = _make_idea(name="Reject Me")
    saved = await db.save_idea(idea)
    resp = await client.post(f"/ideas/{saved.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_delete_endpoint_works(client):
    """DELETE /api/ideas/{id} should remove idea with no repo attached."""
    idea = _make_idea(name="Delete Me")
    saved = await db.save_idea(idea)
    resp = await client.delete(f"/api/ideas/{saved.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"
    gone = await db.get_idea(saved.id)
    assert gone is None
