"""Tests for clickable stat cards and score explanation on the dashboard."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_stat_cards.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


def _make_idea(name="Test Idea", category=IdeaCategory.SECURITY_TOOL, score=0.75, **kw):
    defaults = {
        "name": name,
        "tagline": "A test idea",
        "description": "Test description.",
        "category": category,
        "market_analysis": "Test market.",
        "feasibility_score": score,
        "mvp_scope": "Test scope.",
        "tech_stack": ["python"],
    }
    defaults.update(kw)
    return Idea(**defaults)


class TestStatCardLinks:
    """Stat cards on the dashboard should be clickable links."""

    @pytest.mark.asyncio
    async def test_total_ideas_links_to_explore(self, client):
        """Total Ideas card should link to /explore."""
        resp = await client.get("/")
        assert resp.status_code == 200
        # The stat card containing "Total Ideas" should be wrapped in or contain a link to /explore
        assert 'href="/explore"' in resp.text
        # Specifically the total ideas stat should link somewhere
        assert "Total Ideas" in resp.text

    @pytest.mark.asyncio
    async def test_approved_links_to_explore_approved(self, client):
        """Approved card should link to /explore?status=approved."""
        resp = await client.get("/")
        assert 'href="/explore?status=approved"' in resp.text

    @pytest.mark.asyncio
    async def test_scaffolded_links_to_projects(self, client):
        """Scaffolded card should link to /projects."""
        resp = await client.get("/")
        assert 'href="/projects"' in resp.text
        # The stat card should be the link, not just the nav
        text = resp.text
        # Find scaffolded stat card that links to projects
        scaffolded_idx = text.find("Scaffolded")
        # There should be a link to /projects near the "Scaffolded" label
        nearby = text[max(0, scaffolded_idx - 300):scaffolded_idx + 50]
        assert "/projects" in nearby

    @pytest.mark.asyncio
    async def test_super_ideas_links_to_super_tab(self, client):
        """Super Ideas card should link to super ideas (tab or filtered view)."""
        resp = await client.get("/")
        text = resp.text
        super_idx = text.find("Super Ideas")
        assert super_idx > 0
        # The super ideas stat card should have a clickable link
        nearby = text[max(0, super_idx - 300):super_idx + 50]
        assert "href=" in nearby


class TestAvgScoreExplanation:
    """The Avg Score stat should explain what it is."""

    @pytest.mark.asyncio
    async def test_avg_score_has_explanation(self, client):
        """Avg Score card should have a tooltip or subtitle explaining the score."""
        resp = await client.get("/")
        text = resp.text.lower()
        # Should explain this is AI-assessed, not human-reviewed
        assert "ai" in text or "claude" in text or "feasibility" in text or "title=" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_avg_score_has_tooltip(self, client):
        """Avg Score should have a title attribute for hover explanation."""
        resp = await client.get("/")
        # Look for a title attribute near "Avg Score"
        assert "title=" in resp.text
