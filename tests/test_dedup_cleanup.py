"""Tests for deduplicating existing SI ideas in the database.

Bug: 30 SI ideas exist with massive duplication (e.g., 5x "Dashboard UX Improvements",
4x "Missing Rate Limiting On") because they were inserted before the dedup logic was added.

Fix: A cleanup function that groups SI ideas by normalized tagline, keeps the
highest-scored one per group, and rejects the rest.
"""

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_cleanup.db")
    await d.connect()
    yield d
    await d.close()


def _si_idea(name: str, tagline: str, score: float = 0.8, status: str = "new") -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description="Test description for dedup cleanup.",
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal improvement.",
        feasibility_score=score,
        mvp_scope="Build it.",
        tech_stack=["python"],
        status=status,
    )


class TestDeduplicateExistingSIIdeas:
    """deduplicate_si_ideas scans existing SI ideas and rejects duplicates."""

    @pytest.mark.asyncio
    async def test_keeps_highest_scored_per_group(self, db):
        """Among duplicates, the one with the highest feasibility_score survives."""
        # Insert 3 "dashboard UX" variants directly (bypassing dedup since they predate it)
        ideas = [
            _si_idea("Dashboard V1", "dashboard UX improvements — tailored for test engineering", score=0.65),
            _si_idea("Dashboard V2", "dashboard UX improvements — tailored for DevSecOps", score=0.85),
            _si_idea("Dashboard V3", "dashboard UX improvements — tailored for reliability", score=0.71),
        ]
        for idea in ideas:
            await db.db.execute(
                """INSERT INTO ideas (id, name, tagline, description, category, market_analysis,
                   feasibility_score, mvp_scope, tech_stack, generated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (idea.id, idea.name, idea.tagline, idea.description,
                 idea.category.value, idea.market_analysis, idea.feasibility_score,
                 idea.mvp_scope, "[]", idea.generated_at.isoformat(), idea.status),
            )
        await db.db.commit()

        result = await db.deduplicate_si_ideas()

        remaining = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        active = [i for i in remaining if i.status != "rejected"]
        assert len(active) == 1
        assert active[0].name == "Dashboard V2"  # highest score (0.85)
        assert result["kept"] == 1
        assert result["rejected"] == 2

    @pytest.mark.asyncio
    async def test_preserves_approved_over_new(self, db):
        """If one duplicate is already approved, keep it regardless of score."""
        ideas = [
            _si_idea("Rate Limit V1", "missing rate limiting — tailored for test", score=0.91, status="new"),
            _si_idea("Rate Limit V2", "missing rate limiting — tailored for DevSecOps", score=0.60, status="approved"),
        ]
        for idea in ideas:
            await db.db.execute(
                """INSERT INTO ideas (id, name, tagline, description, category, market_analysis,
                   feasibility_score, mvp_scope, tech_stack, generated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (idea.id, idea.name, idea.tagline, idea.description,
                 idea.category.value, idea.market_analysis, idea.feasibility_score,
                 idea.mvp_scope, "[]", idea.generated_at.isoformat(), idea.status),
            )
        await db.db.commit()

        await db.deduplicate_si_ideas()

        remaining = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        active = [i for i in remaining if i.status != "rejected"]
        assert len(active) == 1
        assert active[0].name == "Rate Limit V2"  # approved beats higher score

    @pytest.mark.asyncio
    async def test_does_not_touch_already_rejected(self, db):
        """Ideas already rejected should not be counted or changed."""
        ideas = [
            _si_idea("Obs V1", "observability additions — tailored for test", score=0.83, status="new"),
            _si_idea("Obs V2", "observability additions — tailored for DevSecOps", score=0.70, status="rejected"),
        ]
        for idea in ideas:
            await db.db.execute(
                """INSERT INTO ideas (id, name, tagline, description, category, market_analysis,
                   feasibility_score, mvp_scope, tech_stack, generated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (idea.id, idea.name, idea.tagline, idea.description,
                 idea.category.value, idea.market_analysis, idea.feasibility_score,
                 idea.mvp_scope, "[]", idea.generated_at.isoformat(), idea.status),
            )
        await db.db.commit()

        result = await db.deduplicate_si_ideas()

        # V1 stays (only active one), V2 was already rejected
        remaining = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        active = [i for i in remaining if i.status != "rejected"]
        assert len(active) == 1
        assert result["rejected"] == 0  # nothing new to reject

    @pytest.mark.asyncio
    async def test_unique_ideas_untouched(self, db):
        """Ideas with genuinely different taglines should all survive."""
        ideas = [
            _si_idea("Rate Limiting", "add rate limiting to API endpoints", score=0.8),
            _si_idea("Structured Logging", "add structured logging with correlation IDs", score=0.75),
            _si_idea("Test Coverage", "automated test coverage enforcement for untested modules", score=0.9),
        ]
        for idea in ideas:
            await db.db.execute(
                """INSERT INTO ideas (id, name, tagline, description, category, market_analysis,
                   feasibility_score, mvp_scope, tech_stack, generated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (idea.id, idea.name, idea.tagline, idea.description,
                 idea.category.value, idea.market_analysis, idea.feasibility_score,
                 idea.mvp_scope, "[]", idea.generated_at.isoformat(), idea.status),
            )
        await db.db.commit()

        result = await db.deduplicate_si_ideas()

        remaining = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        active = [i for i in remaining if i.status != "rejected"]
        assert len(active) == 3
        assert result["rejected"] == 0

    @pytest.mark.asyncio
    async def test_multiple_duplicate_groups_handled(self, db):
        """Multiple groups of duplicates are each independently deduped."""
        ideas = [
            # Group 1: dashboard
            _si_idea("Dash V1", "dashboard UX improvements — tailored for test", score=0.65),
            _si_idea("Dash V2", "dashboard UX improvements — tailored for DevSecOps", score=0.80),
            # Group 2: rate limiting
            _si_idea("Rate V1", "missing rate limiting — tailored for test", score=0.91),
            _si_idea("Rate V2", "missing rate limiting — tailored for reliability", score=0.67),
            _si_idea("Rate V3", "missing rate limiting — tailored for DevSecOps", score=0.88),
            # Unique
            _si_idea("Logging", "add structured logging", score=0.75),
        ]
        for idea in ideas:
            await db.db.execute(
                """INSERT INTO ideas (id, name, tagline, description, category, market_analysis,
                   feasibility_score, mvp_scope, tech_stack, generated_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (idea.id, idea.name, idea.tagline, idea.description,
                 idea.category.value, idea.market_analysis, idea.feasibility_score,
                 idea.mvp_scope, "[]", idea.generated_at.isoformat(), idea.status),
            )
        await db.db.commit()

        result = await db.deduplicate_si_ideas()

        remaining = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        active = [i for i in remaining if i.status != "rejected"]
        names = {i.name for i in active}
        assert len(active) == 3  # 1 dashboard + 1 rate limiting + 1 unique
        assert "Dash V2" in names  # highest dashboard score
        assert "Rate V1" in names  # highest rate limiting score
        assert "Logging" in names  # unique
        assert result["kept"] == 3
        assert result["rejected"] == 3

    @pytest.mark.asyncio
    async def test_returns_summary(self, db):
        """deduplicate_si_ideas returns a summary dict."""
        result = await db.deduplicate_si_ideas()
        assert "kept" in result
        assert "rejected" in result
        assert "groups" in result
