"""Tests for the Challenge feature — ask questions / push back on ideas.

Feature: On the idea detail page, a "Challenge" button lets the user submit a
question or pushback. The system responds (via Claude), and tracks what changed
in the idea's description/scope. A challenge thread is stored per idea.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.config import settings
from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _auth_headers() -> dict:
    """Return Bearer auth headers if api_token is configured."""
    if settings.api_token:
        return {"Authorization": f"Bearer {settings.api_token}"}
    return {}


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_challenge.db"
    await db.connect()
    transport = ASGITransport(app=app)
    headers = _auth_headers()
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c
    await db.close()


def _idea(**kw) -> Idea:
    defaults = dict(
        name="Test Idea",
        tagline="A test idea",
        description="Original description of the idea.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="There is a market.",
        feasibility_score=0.75,
        mvp_scope="Build an MVP with basic features.",
        tech_stack=["python", "fastapi"],
    )
    defaults.update(kw)
    return Idea(**defaults)


# ---------------------------------------------------------------------------
# 1. Challenge model
# ---------------------------------------------------------------------------


class TestChallengeModel:
    """Challenge model holds a question, response, and tracked changes."""

    def test_challenge_has_required_fields(self):
        from project_forge.models import Challenge

        c = Challenge(
            idea_id="abc123",
            question="What about scalability?",
            response="Good point — we should add caching.",
        )
        assert c.idea_id == "abc123"
        assert c.question == "What about scalability?"
        assert c.response == "Good point — we should add caching."
        assert c.id  # auto-generated
        assert c.created_at  # auto-generated

    def test_challenge_has_changes_field(self):
        from project_forge.models import Challenge

        c = Challenge(
            idea_id="abc123",
            question="Is the tech stack right?",
            response="Let me revise.",
            changes=[
                {"field": "tech_stack", "action": "added", "text": "redis"},
                {"field": "mvp_scope", "action": "removed", "text": "manual deployment"},
            ],
        )
        assert len(c.changes) == 2
        assert c.changes[0]["action"] == "added"
        assert c.changes[1]["action"] == "removed"


# ---------------------------------------------------------------------------
# 2. Database: save and list challenges
# ---------------------------------------------------------------------------


class TestChallengeDB:
    """Database stores and retrieves challenges per idea."""

    @pytest_asyncio.fixture
    async def testdb(self, tmp_path):
        from project_forge.storage.db import Database

        d = Database(tmp_path / "test_challenge_db.db")
        await d.connect()
        yield d
        await d.close()

    @pytest.mark.asyncio
    async def test_save_and_list_challenges(self, testdb):
        from project_forge.models import Challenge

        idea = _idea()
        await testdb.save_idea(idea)

        c = Challenge(
            idea_id=idea.id,
            question="What about security?",
            response="We should add auth middleware.",
        )
        await testdb.save_challenge(c)

        challenges = await testdb.list_challenges(idea.id)
        assert len(challenges) == 1
        assert challenges[0].question == "What about security?"
        assert challenges[0].response == "We should add auth middleware."

    @pytest.mark.asyncio
    async def test_list_challenges_returns_empty_for_no_challenges(self, testdb):
        challenges = await testdb.list_challenges("nonexistent")
        assert challenges == []

    @pytest.mark.asyncio
    async def test_challenges_ordered_by_created_at(self, testdb):
        from project_forge.models import Challenge

        idea = _idea()
        await testdb.save_idea(idea)

        c1 = Challenge(idea_id=idea.id, question="First?", response="Yes.")
        c2 = Challenge(idea_id=idea.id, question="Second?", response="Also yes.")
        await testdb.save_challenge(c1)
        await testdb.save_challenge(c2)

        challenges = await testdb.list_challenges(idea.id)
        assert len(challenges) == 2
        assert challenges[0].question == "First?"
        assert challenges[1].question == "Second?"

    @pytest.mark.asyncio
    async def test_challenge_stores_changes(self, testdb):
        from project_forge.models import Challenge

        idea = _idea()
        await testdb.save_idea(idea)

        c = Challenge(
            idea_id=idea.id,
            question="Is redis needed?",
            response="Removed redis, added memcached.",
            changes=[
                {"field": "tech_stack", "action": "removed", "text": "redis"},
                {"field": "tech_stack", "action": "added", "text": "memcached"},
            ],
        )
        await testdb.save_challenge(c)

        challenges = await testdb.list_challenges(idea.id)
        assert len(challenges[0].changes) == 2


# ---------------------------------------------------------------------------
# 3. API: POST challenge, GET challenges
# ---------------------------------------------------------------------------


class TestChallengeAPI:
    """API endpoints for submitting and listing challenges."""

    @pytest.mark.asyncio
    async def test_post_challenge_returns_response(self, client):
        idea = _idea()
        await db.save_idea(idea)

        fake_response = {
            "response": "Good point — adding rate limiting to the MVP scope.",
            "changes": [
                {"field": "mvp_scope", "action": "added", "text": "rate limiting middleware"},
            ],
        }

        with patch("project_forge.web.routes._challenge_idea", return_value=fake_response):
            resp = await client.post(
                f"/api/ideas/{idea.id}/challenge",
                json={"question": "What about rate limiting?"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert data["response"] == fake_response["response"]
        assert len(data["changes"]) == 1

    @pytest.mark.asyncio
    async def test_post_challenge_nonexistent_idea_returns_404(self, client):
        resp = await client.post(
            "/api/ideas/nonexistent/challenge",
            json={"question": "Hello?"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_post_challenge_empty_question_returns_422(self, client):
        idea = _idea()
        await db.save_idea(idea)

        resp = await client.post(
            f"/api/ideas/{idea.id}/challenge",
            json={"question": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_get_challenges_returns_list(self, client):
        from project_forge.models import Challenge

        idea = _idea()
        await db.save_idea(idea)

        c = Challenge(idea_id=idea.id, question="Why Python?", response="Best for prototyping.")
        await db.save_challenge(c)

        resp = await client.get(f"/api/ideas/{idea.id}/challenges")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["question"] == "Why Python?"


# ---------------------------------------------------------------------------
# 4. Idea detail page shows challenge UI
# ---------------------------------------------------------------------------


class TestChallengeInTemplate:
    """The idea detail page includes the challenge button and thread."""

    @pytest.mark.asyncio
    async def test_idea_detail_has_challenge_button(self, client):
        idea = _idea()
        await db.save_idea(idea)

        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        assert 'data-action="challenge-idea"' in resp.text

    @pytest.mark.asyncio
    async def test_idea_detail_has_challenge_section(self, client):
        idea = _idea()
        await db.save_idea(idea)

        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        assert "challenge-section" in resp.text
