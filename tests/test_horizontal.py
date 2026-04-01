"""Tests for horizontal expansion — cross-category idea generation."""

from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.cron.horizontal import (
    generate_cross_idea,
    pick_cross_category_pair,
    run_horizontal_cycle,
)
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_horizontal.db")
    await database.connect()
    yield database
    await database.close()


@pytest_asyncio.fixture
async def seeded_db(tmp_path: Path):
    """DB with 50 ideas for clustering/super idea tests."""
    database = Database(tmp_path / "test_horizontal_seeded.db")
    await database.connect()
    await run_auto_scan(database, count=50)
    yield database
    await database.close()


class TestCategoryPairLog:
    """Test the category_pair_log DB table and methods."""

    @pytest.mark.asyncio
    async def test_record_category_pair(self, db):
        await db.record_category_pair("automation", "security-tool", "idea123")
        pairs = await db.get_least_explored_pairs(limit=66)
        # The recorded pair should appear with count=1
        recorded = [p for p in pairs if p[0] == "automation" and p[1] == "security-tool"]
        assert len(recorded) == 1
        assert recorded[0][2] == 1

    @pytest.mark.asyncio
    async def test_pair_normalization(self, db):
        """cat_a < cat_b alphabetically, regardless of insertion order."""
        await db.record_category_pair("security-tool", "automation", "idea1")
        await db.record_category_pair("automation", "security-tool", "idea2")
        pairs = await db.get_least_explored_pairs(limit=66)
        # Both should be normalized to (automation, security-tool)
        matching = [p for p in pairs if p[0] == "automation" and p[1] == "security-tool"]
        assert len(matching) == 1
        assert matching[0][2] == 2

    @pytest.mark.asyncio
    async def test_least_explored_returns_zeros(self, db):
        """Pairs with no entries should appear with count=0."""
        pairs = await db.get_least_explored_pairs(limit=66)
        assert len(pairs) == 66  # 12 choose 2
        assert all(p[2] == 0 for p in pairs)

    @pytest.mark.asyncio
    async def test_least_explored_ordering(self, db):
        """Pairs should be sorted by count ascending."""
        await db.record_category_pair("automation", "security-tool", "idea1")
        await db.record_category_pair("automation", "security-tool", "idea2")
        await db.record_category_pair("compliance", "privacy", "idea3")
        pairs = await db.get_least_explored_pairs(limit=66)
        counts = [p[2] for p in pairs]
        assert counts == sorted(counts)


class TestPickCrossCategoryPair:
    @pytest.mark.asyncio
    async def test_returns_two_different_categories(self, db):
        cat_a, cat_b = await pick_cross_category_pair(db)
        assert isinstance(cat_a, IdeaCategory)
        assert isinstance(cat_b, IdeaCategory)
        assert cat_a != cat_b

    @pytest.mark.asyncio
    async def test_picks_unexplored_pair(self, db):
        """After recording one pair, should pick a different one."""
        # Record many ideas for one pair to push it to the bottom
        for i in range(10):
            await db.record_category_pair("automation", "security-tool", f"idea{i}")
        cat_a, cat_b = await pick_cross_category_pair(db)
        # Should not pick the heavily-explored pair
        pair = tuple(sorted([cat_a.value, cat_b.value]))
        assert pair != ("automation", "security-tool")

    @pytest.mark.asyncio
    async def test_exclude_parameter(self, db):
        cat_a, cat_b = await pick_cross_category_pair(db)
        pair1 = (cat_a, cat_b)
        cat_c, cat_d = await pick_cross_category_pair(db, exclude=[pair1])
        pair2 = tuple(sorted([cat_c.value, cat_d.value]))
        pair1_sorted = tuple(sorted([cat_a.value, cat_b.value]))
        assert pair2 != pair1_sorted


class TestGenerateCrossIdea:
    @pytest.mark.asyncio
    async def test_generates_valid_idea(self, db):
        idea = await generate_cross_idea(
            db, IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.COMPLIANCE
        )
        assert isinstance(idea, Idea)
        assert idea.name
        assert idea.description
        assert idea.feasibility_score > 0

    @pytest.mark.asyncio
    async def test_cross_idea_has_crossover_content(self, db):
        idea = await generate_cross_idea(
            db, IdeaCategory.SECURITY_TOOL, IdeaCategory.PRIVACY
        )
        # The description should reference cross-domain concepts
        assert "cross" in idea.description.lower() or "meets" in idea.description.lower()


class TestRunHorizontalCycle:
    @pytest.mark.asyncio
    async def test_returns_two_ideas(self, seeded_db):
        ideas = await run_horizontal_cycle(seeded_db)
        assert len(ideas) == 2

    @pytest.mark.asyncio
    async def test_ideas_stored_in_db(self, seeded_db):
        ideas = await run_horizontal_cycle(seeded_db)
        for idea in ideas:
            stored = await seeded_db.get_idea(idea.id)
            assert stored is not None

    @pytest.mark.asyncio
    async def test_records_category_pairs(self, seeded_db):
        await run_horizontal_cycle(seeded_db)
        pairs = await seeded_db.get_least_explored_pairs(limit=66)
        explored = [p for p in pairs if p[2] > 0]
        assert len(explored) >= 1

    @pytest.mark.asyncio
    async def test_multiple_cycles_explore_different_pairs(self, seeded_db):
        """Running multiple cycles should explore different category pairs."""
        all_ideas = []
        for _ in range(3):
            ideas = await run_horizontal_cycle(seeded_db)
            all_ideas.extend(ideas)
        # Should have 6 ideas total
        assert len(all_ideas) == 6
        # At least 2 different categories should be represented
        categories = {i.category for i in all_ideas}
        assert len(categories) >= 2
