"""TDD: Super idea display dedup and generation-time prevention.

Problem: Dashboard shows duplicate super ideas that are the same base concept
with different perspective suffixes:
  - "[SUPER] Autonomous Security Testing Platform"
  - "[SUPER] Autonomous Security Testing Platform (Attack & Defense)"
  - "[SUPER] Autonomous Security Testing Platform (Attack & Defense)"  (different category)

Three gaps:
1. list_super_ideas() groups by full name, not base name
2. should_accept() skips fuzzy dedup entirely for super ideas
3. No base-name dedup at the generation/acceptance layer

Fix targets:
- list_super_ideas: SQL groups by base name (strip parenthetical suffixes)
- should_accept: add base-name check for [SUPER] ideas
"""

import pytest
import pytest_asyncio

from project_forge.engine.dedup import should_accept
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_super_display.db")
    await d.connect()
    yield d
    await d.close()


def _super(
    name: str,
    score: float = 0.92,
    category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
) -> Idea:
    return Idea(
        name=name,
        tagline=f"Unified platform for {name}",
        description="A mega project.",
        category=category,
        market_analysis="Big market.",
        feasibility_score=score,
        mvp_scope="Phase 1, 2, 3.",
        tech_stack=["python", "rust"],
    )


# ── list_super_ideas: display-time dedup by base name ───────────────


class TestListSuperIdeasBaseName:
    """list_super_ideas must group by base name, not full name."""

    @pytest.mark.asyncio
    async def test_same_base_name_different_suffix_returns_one(self, db):
        """Three variants of the same concept → only the best one shown."""
        await db.save_idea(_super("[SUPER] Threat Engine", score=0.90))
        await db.save_idea(
            _super("[SUPER] Threat Engine (Attack & Defense)", score=0.95)
        )
        await db.save_idea(
            _super(
                "[SUPER] Threat Engine (PQC & Crypto)",
                score=0.88,
                category=IdeaCategory.PQC_CRYPTOGRAPHY,
            )
        )

        result = await db.list_super_ideas(limit=6)
        names = [r.name for r in result]
        # Only one entry for "Threat Engine" base name
        assert len(result) == 1, f"Expected 1 super idea, got {len(result)}: {names}"
        # Kept the highest-scored variant
        assert result[0].feasibility_score == 0.95

    @pytest.mark.asyncio
    async def test_different_base_names_all_shown(self, db):
        """Genuinely different super ideas should all appear."""
        await db.save_idea(_super("[SUPER] Threat Engine", score=0.90))
        await db.save_idea(_super("[SUPER] DevOps Platform", score=0.88))
        await db.save_idea(_super("[SUPER] Privacy Suite", score=0.85))

        result = await db.list_super_ideas(limit=6)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_archived_variants_excluded(self, db):
        """Archived super ideas should not appear even if highest-scored."""
        await db.save_idea(_super("[SUPER] Threat Engine", score=0.90))
        # Save a higher-scored variant, then archive it
        high = _super("[SUPER] Threat Engine (Attack & Defense)", score=0.95)
        await db.save_idea(high)
        await db.db.execute(
            "UPDATE ideas SET status = 'archived' WHERE id = ?", (high.id,)
        )
        await db.db.commit()

        result = await db.list_super_ideas(limit=6)
        assert len(result) == 1
        # Should show the non-archived one
        assert result[0].feasibility_score == 0.90

    @pytest.mark.asyncio
    async def test_respects_limit(self, db):
        """Limit should apply after base-name dedup, not before."""
        for i in range(5):
            await db.save_idea(_super(f"[SUPER] Project {i}", score=0.80 + i * 0.02))
            await db.save_idea(
                _super(f"[SUPER] Project {i} (Variant)", score=0.79 + i * 0.02)
            )

        result = await db.list_super_ideas(limit=3)
        assert len(result) == 3
        # All should be different base names
        base_names = set()
        for r in result:
            raw = r.name.replace("[SUPER] ", "")
            import re

            base = re.sub(r"\s*\([^)]+\)\s*$", "", raw).strip()
            base_names.add(base)
        assert len(base_names) == 3


# ── should_accept: generation-time prevention ────────────────────────


class TestShouldAcceptSuperDedup:
    """should_accept must reject super ideas with duplicate base names."""

    @pytest.mark.asyncio
    async def test_rejects_same_base_name_with_suffix(self, db):
        """New super idea with matching base name (different suffix) → reject."""
        await db.save_idea(_super("[SUPER] Threat Engine"))

        candidate = _super("[SUPER] Threat Engine (Attack & Defense)")
        accepted, reason = await should_accept(candidate, db)

        assert not accepted
        assert reason is not None
        assert "duplicate" in reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_same_base_name_exact(self, db):
        """Exact same super idea name → reject."""
        await db.save_idea(_super("[SUPER] Threat Engine"))

        candidate = _super("[SUPER] Threat Engine")
        # Different content_hash since it's a new Idea object with different id
        candidate.content_hash = None
        accepted, reason = await should_accept(candidate, db)

        assert not accepted
        assert reason is not None

    @pytest.mark.asyncio
    async def test_accepts_different_super_idea(self, db):
        """Genuinely different super idea → accept."""
        await db.save_idea(_super("[SUPER] Threat Engine"))

        candidate = _super("[SUPER] Privacy Platform")
        accepted, reason = await should_accept(candidate, db)

        assert accepted
        assert reason is None

    @pytest.mark.asyncio
    async def test_accepts_non_super_idea(self, db):
        """Regular ideas bypass super-specific dedup (handled by tagline check)."""
        await db.save_idea(_super("[SUPER] Threat Engine"))

        regular = Idea(
            name="Threat Engine Lite",
            tagline="A lightweight threat engine",
            description="Desc.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market.",
            feasibility_score=0.8,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        accepted, reason = await should_accept(regular, db)

        assert accepted

    @pytest.mark.asyncio
    async def test_rejects_different_category_same_base(self, db):
        """Same base name but different category → still rejected."""
        await db.save_idea(
            _super("[SUPER] Threat Engine", category=IdeaCategory.SECURITY_TOOL)
        )

        candidate = _super(
            "[SUPER] Threat Engine (Attack & Defense)",
            category=IdeaCategory.VULNERABILITY_RESEARCH,
        )
        accepted, reason = await should_accept(candidate, db)

        assert not accepted
