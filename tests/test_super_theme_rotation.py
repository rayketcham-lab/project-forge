"""Tests for coverage-aware super-idea slot selection.

The original `slot = datetime.now(UTC).hour % 5` rotation is mechanical;
it visits the same slot every 5 hours regardless of how saturated that
slot's categories already are. The result was the corpus inspection
showed top 6 super themes accounting for ~20% of all supers — the
clustering kept hammering whatever was densest.

`pick_least_covered_slot` picks the slot whose `seed_categories` carry
the fewest active super-ideas, so under-explored corners get attention.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _super_in_category(name: str, cat: IdeaCategory, score: float = 0.85) -> Idea:
    return Idea(
        name=f"[SUPER] {name}",
        tagline="t",
        description="d",
        category=cat,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "rotation.db")
    await database.connect()
    yield database
    await database.close()


class TestLeastCoveredSlot:
    @pytest.mark.asyncio
    async def test_empty_db_returns_slot_zero(self, db):
        from project_forge.engine.super_ideas import pick_least_covered_slot

        slot = await pick_least_covered_slot(db)
        assert slot == 0

    @pytest.mark.asyncio
    async def test_avoids_saturated_slot(self, db):
        """If slot 0 (PQC & Crypto) already has many supers, return another."""
        from project_forge.engine.super_ideas import pick_least_covered_slot

        for i in range(5):
            await db.save_idea(
                _super_in_category(
                    f"PQC Item {i}",
                    IdeaCategory.PQC_CRYPTOGRAPHY,
                )
            )
        slot = await pick_least_covered_slot(db)
        assert slot != 0

    @pytest.mark.asyncio
    async def test_picks_truly_least_covered(self, db):
        """Slots 0, 1, 3, 4 each have one super; slot 2 has none → pick 2."""
        from project_forge.engine.super_ideas import pick_least_covered_slot

        await db.save_idea(_super_in_category("a", IdeaCategory.PQC_CRYPTOGRAPHY))
        await db.save_idea(_super_in_category("b", IdeaCategory.NIST_STANDARDS))
        await db.save_idea(_super_in_category("c", IdeaCategory.DEVOPS_TOOLING))
        await db.save_idea(_super_in_category("d", IdeaCategory.PRIVACY))
        # Slot 2 (Attack & Defense) gets nothing.
        slot = await pick_least_covered_slot(db)
        assert slot == 2

    @pytest.mark.asyncio
    async def test_ignores_archived_supers(self, db):
        from project_forge.engine.super_ideas import pick_least_covered_slot

        for i in range(10):
            idea = _super_in_category(f"x{i}", IdeaCategory.PQC_CRYPTOGRAPHY)
            await db.save_idea(idea)
            await db.update_idea_status(idea.id, "archived")
        # Even with 10 archived, slot 0 is still empty among ACTIVE supers.
        slot = await pick_least_covered_slot(db)
        assert slot == 0
