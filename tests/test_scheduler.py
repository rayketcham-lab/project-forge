"""Tests for `project_forge.cron.scheduler.run_full_cycle` — fix #72.

generate_and_store explicitly returns None on four paths (quality
review fail, dedup gate, router discard, router contribute). Before
fix #72, run_full_cycle called downstream methods on that None and
crashed with AttributeError. The guard converts the filtered-idea
outcome into a clean early-return.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from project_forge.cron.scheduler import run_full_cycle
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "scheduler.db")
    await d.connect()
    yield d
    await d.close()


def _idea(name: str = "Cycle Test") -> Idea:
    return Idea(
        name=name,
        tagline="t",
        description="d" * 80,
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="m" * 40,
        feasibility_score=0.75,
        mvp_scope="mvp" * 5,
        tech_stack=["python", "fastapi"],
    )


class TestRunFullCycleNoneGuard:
    @pytest.mark.asyncio
    async def test_returns_none_when_generate_returns_none(self, db):
        """Filtered ideas (dedup gate, quality review fail, etc.) come back
        as None from generate_and_store. run_full_cycle must propagate that
        cleanly without crashing."""
        with patch(
            "project_forge.cron.scheduler.generate_and_store",
            new=AsyncMock(return_value=None),
        ):
            result = await run_full_cycle(db, generator=object())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_idea_on_happy_path(self, db):
        """Sanity check: when generate succeeds, the idea is returned even
        if downstream GH/scaffold steps fail (they're wrapped in try)."""
        idea = _idea()
        with (
            patch(
                "project_forge.cron.scheduler.generate_and_store",
                new=AsyncMock(return_value=idea),
            ),
            patch(
                "project_forge.cron.scheduler.create_github_issue_for_idea",
                new=AsyncMock(side_effect=RuntimeError("gh down")),
            ),
            patch(
                "project_forge.cron.scheduler.is_high_value",
                return_value=False,
            ),
        ):
            result = await run_full_cycle(db, generator=object())
        assert result is idea
