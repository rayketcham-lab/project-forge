"""Tests for the `challenged=1` filter on /explore.

User asked for "a UI button somewhere to view challenged explore items" —
the route is the floor of that, and the dashboard hero-stat link is the
button surface. These tests pin both behaviors.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Challenge, Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "challenged.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


def _make_idea(name: str) -> Idea:
    return Idea(
        name=name,
        tagline=f"tagline for {name}",
        description=(
            "A long enough description body to pass the dedup quality "
            "review minimum length for the engine — at least 50 chars."
        ),
        category=IdeaCategory.AUTOMATION,
        market_analysis="Some plausible market analysis for the idea.",
        feasibility_score=0.8,
        mvp_scope="Minimum viable scope for the idea.",
        tech_stack=["python"],
    )


def _make_challenge(idea_id: str) -> Challenge:
    return Challenge(
        idea_id=idea_id,
        question="Why would users adopt this?",
        challenge_type="freeform",
        focus_area="all",
        tone="skeptical",
        response="Because of X, Y, Z.",
        changes=[],
        created_at=datetime.now(UTC),
    )


class TestListChallengedIdeas:
    """The DB layer must expose a way to find ideas with >=1 challenge."""

    @pytest.mark.asyncio
    async def test_list_challenged_returns_only_challenged(self, client):
        """list_challenged_ideas returns ideas that have at least one challenge."""
        without = _make_idea("Plain Idea")
        with_chal = _make_idea("Challenged Idea")
        await db.save_idea(without)
        await db.save_idea(with_chal)
        await db.save_challenge(_make_challenge(with_chal.id))

        result = await db.list_challenged_ideas(limit=100)
        ids = {i.id for i in result}
        assert with_chal.id in ids
        assert without.id not in ids

    @pytest.mark.asyncio
    async def test_count_challenged_returns_distinct(self, client):
        """count_challenged_ideas counts ideas, not challenges (one idea, two challenges = 1)."""
        idea = _make_idea("Twice-Challenged")
        await db.save_idea(idea)
        await db.save_challenge(_make_challenge(idea.id))
        await db.save_challenge(_make_challenge(idea.id))

        total = await db.count_challenged_ideas()
        assert total == 1


class TestExploreChallengedFilter:
    """The /explore route honors ?challenged=1."""

    @pytest.mark.asyncio
    async def test_explore_challenged_renders_only_challenged_cards(self, client):
        """/explore?challenged=1 returns 200 and renders only challenged ideas."""
        plain = _make_idea("Plain Idea")
        challenged = _make_idea("Challenged Idea")
        await db.save_idea(plain)
        await db.save_idea(challenged)
        await db.save_challenge(_make_challenge(challenged.id))

        response = await client.get("/explore?challenged=1")
        assert response.status_code == 200
        body = response.text
        assert "Challenged Idea" in body
        assert "Plain Idea" not in body

    @pytest.mark.asyncio
    async def test_explore_no_filter_returns_all(self, client):
        """Without ?challenged, both ideas appear."""
        plain = _make_idea("Plain Idea")
        challenged = _make_idea("Challenged Idea")
        await db.save_idea(plain)
        await db.save_idea(challenged)
        await db.save_challenge(_make_challenge(challenged.id))

        response = await client.get("/explore")
        assert response.status_code == 200
        body = response.text
        assert "Challenged Idea" in body
        assert "Plain Idea" in body


class TestDashboardChallengedButton:
    """The dashboard exposes a button/link to /explore?challenged=1."""

    @pytest.mark.asyncio
    async def test_dashboard_links_challenges_stat_to_filter(self, client):
        """Clicking the 'Challenges Filed' stat must land on the filtered explore."""
        response = await client.get("/")
        assert response.status_code == 200
        body = response.text
        # The hero-activity-item that wraps "Challenges Filed" must be a link
        # pointing at the new filter so the click target exists.
        assert "/explore?challenged=1" in body
