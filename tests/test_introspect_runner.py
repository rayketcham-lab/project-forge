"""Tests for the introspection cron runner and schedule messaging."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_introspect.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


# --- Cron runner tests ---


class TestIntrospectRunner:
    """Tests for the introspection cron entry point."""

    @pytest.mark.asyncio
    async def test_run_introspect_cycle_generates_idea(self):
        """run_introspect_cycle should generate and store a self-improvement idea."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        mock_generator = MagicMock()
        fake_idea = Idea(
            name="Add structured logging",
            tagline="Better observability",
            description=(
                "The engine module in src/project_forge/engine/ lacks structured logging. "
                "Add structlog with correlation IDs for better observability and debugging."
            ),
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Improves debugging and observability of the forge engine.",
            feasibility_score=0.85,
            mvp_scope="Add structlog to src/project_forge/engine/ and tests/test_logging.py.",
            tech_stack=["python", "structlog"],
        )
        mock_generator.generate = AsyncMock(return_value=fake_idea)

        with patch(
            "project_forge.cron.introspect_runner.gather_self_context",
            return_value={
                "open_issues": [],
                "recent_commits": [],
                "test_count": 10,
                "lint_status": "clean",
                "code_stats": {"src": 1000, "tests": 500},
            },
        ):
            idea = await run_introspect_cycle(mock_db, mock_generator)

        assert idea is not None
        assert idea.category == IdeaCategory.SELF_IMPROVEMENT
        mock_db.save_idea.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_introspect_cycle_avoids_recent_names(self):
        """run_introspect_cycle passes recent self-improvement names to prompt builder."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        existing = Idea(
            name="Old improvement",
            tagline="Already suggested",
            description="Already suggested.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Internal.",
            feasibility_score=0.7,
            mvp_scope="Done.",
            tech_stack=["python"],
        )
        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[existing])
        mock_db.save_idea = AsyncMock()

        mock_generator = MagicMock()
        fake_idea = Idea(
            name="New improvement",
            tagline="Fresh idea",
            description="Something new.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Internal.",
            feasibility_score=0.8,
            mvp_scope="Build it.",
            tech_stack=["python"],
        )
        mock_generator.generate = AsyncMock(return_value=fake_idea)

        with (
            patch(
                "project_forge.cron.introspect_runner.gather_self_context",
                return_value={
                    "open_issues": [],
                    "recent_commits": [],
                    "test_count": 10,
                    "lint_status": "clean",
                    "code_stats": {},
                },
            ),
            patch("project_forge.cron.introspect_runner.build_introspection_prompt") as mock_prompt,
        ):
            mock_prompt.return_value = "fake prompt"
            await run_introspect_cycle(mock_db, mock_generator)

        # Should have passed "Old improvement" as a recent name to avoid
        mock_prompt.assert_called_once()
        recent_names = mock_prompt.call_args[0][1]
        assert "Old improvement" in recent_names


# --- Empty state message tests ---


class TestEmptyStateMessage:
    """Tests that the empty state shows schedule info."""

    @pytest.mark.asyncio
    async def test_empty_proposals_shows_schedule_info(self, client):
        """When no proposals exist, the empty state should mention the schedule."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert resp.status_code == 200
        text = resp.text.lower()
        # Should mention when introspection runs
        assert "daily" in text or "schedule" in text or "hourly" in text or "runs" in text

    @pytest.mark.asyncio
    async def test_empty_proposals_shows_tip(self, client):
        """When no proposals exist, should show a tip or guidance."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        text = resp.text.lower()
        assert "tip" in text or "introspect" in text or "generate" in text
