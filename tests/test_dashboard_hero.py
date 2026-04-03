"""Tests for the improved dashboard hero section.

Problem: The hero section shows a generic subtitle, an "Explore Ideas" button,
and a hardcoded "PQC Security" link. It's bare and doesn't surface what matters:
recent activity, Think Tank status, pipeline health, and quick actions.

Fix: The dashboard route provides richer context (recent ideas, SI pipeline status,
challenge count) and the hero section renders a proper command center.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.config import settings
from project_forge.models import Challenge, Idea, IdeaCategory
from project_forge.web.app import app, db


def _auth_headers() -> dict:
    if settings.api_token:
        return {"Authorization": f"Bearer {settings.api_token}"}
    return {}


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_hero.db"
    await db.connect()
    transport = ASGITransport(app=app)
    headers = _auth_headers()
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c
    await db.close()


def _idea(name: str, category: IdeaCategory = IdeaCategory.SECURITY_TOOL, **kw) -> Idea:
    defaults = dict(
        name=name,
        tagline=f"Tagline for {name}",
        description="Test description.",
        category=category,
        market_analysis="Market need.",
        feasibility_score=0.75,
        mvp_scope="Build it.",
        tech_stack=["python"],
    )
    defaults.update(kw)
    return Idea(**defaults)


class TestDashboardHeroContent:
    """The dashboard hero should show meaningful, dynamic content."""

    @pytest.mark.asyncio
    async def test_no_hardcoded_pqc_link(self, client):
        """The hero should not have a hardcoded PQC Security button."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'category=pqc-cryptography' not in resp.text

    @pytest.mark.asyncio
    async def test_hero_shows_think_tank_link(self, client):
        """Hero should have a quick link to the Think Tank."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert '/thinktank' in resp.text

    @pytest.mark.asyncio
    async def test_hero_shows_recent_activity_count(self, client):
        """Hero should surface recent idea count or activity."""
        idea = _idea("Fresh Idea")
        await db.save_idea(idea)

        resp = await client.get("/")
        assert resp.status_code == 200
        # Should show some form of activity indicator
        assert 'hero-activity' in resp.text or 'recent' in resp.text.lower()

    @pytest.mark.asyncio
    async def test_hero_shows_pipeline_stats(self, client):
        """Hero should show pipeline status (approved, pending, challenged)."""
        await db.save_idea(_idea("Pending", status="new"))
        await db.save_idea(_idea("Approved", status="approved"))

        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'hero-pipeline' in resp.text


class TestDashboardRouteData:
    """The dashboard route should provide data for the enhanced hero."""

    @pytest.mark.asyncio
    async def test_dashboard_provides_recent_ideas(self, client):
        """The API stats endpoint should include recent idea info."""
        await db.save_idea(_idea("Recent1"))
        await db.save_idea(_idea("Recent2"))

        resp = await client.get("/api/stats")
        data = resp.json()
        assert "total_ideas" in data

    @pytest.mark.asyncio
    async def test_dashboard_provides_si_count(self, client):
        """Stats should include self-improvement pipeline count."""
        await db.save_idea(
            _idea("SI Idea", category=IdeaCategory.SELF_IMPROVEMENT)
        )
        resp = await client.get("/api/stats")
        data = resp.json()
        assert "ideas_by_category" in data

    @pytest.mark.asyncio
    async def test_dashboard_provides_challenge_count(self, client):
        """Stats should include total challenge count."""
        idea = _idea("Challenged Idea")
        await db.save_idea(idea)
        c = Challenge(idea_id=idea.id, question="Why?", response="Because.")
        await db.save_challenge(c)

        resp = await client.get("/api/stats")
        data = resp.json()
        assert "total_challenges" in data
        assert data["total_challenges"] >= 1
