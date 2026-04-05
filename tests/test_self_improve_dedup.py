"""Tests for self-improvement idea deduplication.

Bug: The introspection engine generates near-identical self-improvement ideas
like "Dashboard Ux Improvements And Suite" vs "Dashboard Ux Improvements And Engine"
— same core idea with a different suffix. These all pass the exact-name dedup check
and flood the database.

Fix: Fuzzy dedup on tagline similarity for self-improvement ideas at save time.
"""

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_dedup.db")
    await d.connect()
    yield d
    await d.close()


def _si_idea(name: str, tagline: str, **kw) -> Idea:
    """Create a self-improvement idea."""
    return Idea(
        name=name,
        tagline=tagline,
        description=kw.get("description", "Test description."),
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal improvement.",
        feasibility_score=0.8,
        mvp_scope="Build it.",
        tech_stack=["python"],
        **{k: v for k, v in kw.items() if k != "description"},
    )


def _regular_idea(name: str, tagline: str) -> Idea:
    """Create a non-self-improvement idea."""
    return Idea(
        name=name,
        tagline=tagline,
        description="Test description.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Market need.",
        feasibility_score=0.7,
        mvp_scope="Build it.",
        tech_stack=["python"],
    )


# ---------------------------------------------------------------------------
# 1. Similarity function unit tests
# ---------------------------------------------------------------------------


class TestTaglineSimilarity:
    """tagline_similarity returns a 0.0–1.0 score for two taglines."""

    def test_identical_taglines_return_1(self):
        from project_forge.engine.dedup import tagline_similarity

        score = tagline_similarity(
            "dashboard UX improvements and accessibility gaps",
            "dashboard UX improvements and accessibility gaps",
        )
        assert score == 1.0

    def test_completely_different_return_near_zero(self):
        from project_forge.engine.dedup import tagline_similarity

        score = tagline_similarity(
            "dashboard UX improvements and accessibility gaps",
            "add rate limiting to generation endpoints",
        )
        assert score < 0.3

    def test_near_duplicates_with_different_suffix(self):
        from project_forge.engine.dedup import tagline_similarity

        score = tagline_similarity(
            "dashboard UX improvements and accessibility gaps — tailored for developer experience",
            "dashboard UX improvements and accessibility gaps — tailored for test engineering",
        )
        # Same core idea, different suffix — should be very high similarity
        assert score > 0.7

    def test_case_insensitive(self):
        from project_forge.engine.dedup import tagline_similarity

        score = tagline_similarity(
            "Dashboard UX Improvements",
            "dashboard ux improvements",
        )
        assert score == 1.0

    def test_empty_strings(self):
        from project_forge.engine.dedup import tagline_similarity

        assert tagline_similarity("", "") == 1.0
        assert tagline_similarity("something", "") == 0.0


# ---------------------------------------------------------------------------
# 2. Database-level dedup: near-duplicate SI ideas rejected
# ---------------------------------------------------------------------------


class TestSelfImprovementDedup:
    """save_idea should reject near-duplicate self-improvement ideas."""

    @pytest.mark.asyncio
    async def test_exact_duplicate_tagline_rejected(self, db):
        """Saving a SI idea with an identical tagline to an existing one is skipped."""
        original = _si_idea("Dashboard Fix V1", "dashboard UX improvements and accessibility gaps")
        await db.save_idea(original)

        duplicate = _si_idea("Dashboard Fix V2", "dashboard UX improvements and accessibility gaps")
        await db.save_idea(duplicate)

        # Should still return the idea (silent skip), but NOT store it
        all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        assert len(all_si) == 1
        assert all_si[0].name == "Dashboard Fix V1"

    @pytest.mark.asyncio
    async def test_near_duplicate_tagline_rejected(self, db):
        """Saving a SI idea with a near-duplicate tagline should be skipped."""
        original = _si_idea(
            "Dashboard Ux Improvements And Suite",
            "dashboard UX improvements and accessibility gaps — tailored for reliability engineering",
        )
        await db.save_idea(original)

        near_dup = _si_idea(
            "Dashboard Ux Improvements And Engine",
            "dashboard UX improvements and accessibility gaps — tailored for test engineering",
        )
        await db.save_idea(near_dup)

        all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        assert len(all_si) == 1

    @pytest.mark.asyncio
    async def test_different_si_ideas_both_saved(self, db):
        """Two genuinely different SI ideas should both be saved."""
        idea1 = _si_idea("Rate Limiting", "add rate limiting to API endpoints")
        idea2 = _si_idea("Structured Logging", "add structured logging with correlation IDs")
        await db.save_idea(idea1)
        await db.save_idea(idea2)

        all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        assert len(all_si) == 2

    @pytest.mark.asyncio
    async def test_non_si_ideas_also_deduped(self, db):
        """Regular (non-SI) ideas with identical taglines are now deduped (universal dedup)."""
        idea1 = _regular_idea("PKI Scanner V1", "scan PKI infrastructure for cert issues")
        idea2 = _regular_idea("PKI Scanner V2", "scan PKI infrastructure for cert issues")
        await db.save_idea(idea1)
        await db.save_idea(idea2)

        all_ideas = await db.list_ideas(limit=100)
        assert len(all_ideas) == 1, "Universal dedup should block near-duplicate non-SI ideas"

    @pytest.mark.asyncio
    async def test_cross_category_dedup_scoped(self, db):
        """A regular idea with a similar tagline to an SI idea should still save (different category)."""
        si = _si_idea("Dashboard Fix", "dashboard UX improvements")
        regular = _regular_idea("Dashboard Scanner", "dashboard UX improvements")
        await db.save_idea(si)
        await db.save_idea(regular)

        all_ideas = await db.list_ideas(limit=100)
        assert len(all_ideas) == 2, "Dedup is scoped to same category"


# ---------------------------------------------------------------------------
# 3. Introspection runner uses dedup (integration)
# ---------------------------------------------------------------------------


class TestIntrospectionDedup:
    """The introspection runner should not store near-duplicate ideas."""

    @pytest.mark.asyncio
    async def test_introspect_cycle_skips_near_dup(self, db):
        """If the generated idea is a near-dup of an existing one, it's not stored."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from project_forge.cron.introspect_runner import run_introspect_cycle

        # Pre-seed with an existing SI idea
        existing = _si_idea(
            "Dashboard Ux Improvements And Suite",
            "dashboard UX improvements and accessibility gaps — tailored for reliability engineering",
        )
        await db.save_idea(existing)

        # The generator will return a near-duplicate
        near_dup = _si_idea(
            "Dashboard Ux Improvements And Engine",
            "dashboard UX improvements and accessibility gaps — tailored for test engineering",
        )
        mock_gen = MagicMock()
        mock_gen.generate = AsyncMock(return_value=near_dup)

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
            await run_introspect_cycle(db, mock_gen)

        # Should still only have the original
        all_si = await db.list_ideas(category=IdeaCategory.SELF_IMPROVEMENT, limit=100)
        assert len(all_si) == 1
        assert all_si[0].name == "Dashboard Ux Improvements And Suite"
