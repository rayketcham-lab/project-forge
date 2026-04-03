"""Tests for super idea deduplication and generation prevention.

Problem: 60 super ideas with massive duplication — same base name with different
suffixes like "(Attack & Defense)", "(Platform & DevOps)". The generate_seeded()
method appends suffixes instead of skipping duplicates.

Fix:
1. Dedup existing super ideas (keep highest-scored per base name)
2. Prevent future duplication with fuzzy name matching at generation time
"""

import re

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_super_dedup.db")
    await d.connect()
    yield d
    await d.close()


def _super(name: str, score: float = 0.92, category=IdeaCategory.SECURITY_TOOL) -> Idea:
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


class TestDeduplicateSuperIdeas:
    """deduplicate_super_ideas cleans up duplicate super ideas by base name."""

    @pytest.mark.asyncio
    async def test_keeps_one_per_base_name(self, db):
        """Multiple supers with same base name (different suffixes) → keep best."""
        await db.save_idea(_super("[SUPER] Threat Engine", score=0.90))
        await db.save_idea(_super("[SUPER] Threat Engine (Attack & Defense)", score=0.92))
        await db.save_idea(_super("[SUPER] Threat Engine (PQC & Crypto)", score=0.88))

        result = await db.deduplicate_super_ideas()

        supers = await db.list_ideas(limit=100)
        active = [i for i in supers if i.status not in ("rejected", "archived")]
        assert len(active) == 1
        assert active[0].feasibility_score == 0.92  # kept best
        assert result["kept"] >= 1
        assert result["archived"] == 2

    @pytest.mark.asyncio
    async def test_different_base_names_preserved(self, db):
        """Genuinely different super ideas should all survive."""
        await db.save_idea(_super("[SUPER] Threat Engine"))
        await db.save_idea(_super("[SUPER] DevOps Platform"))
        await db.save_idea(_super("[SUPER] Privacy Suite"))

        result = await db.deduplicate_super_ideas()

        supers = await db.list_ideas(limit=100)
        active = [i for i in supers if i.status not in ("rejected", "archived")]
        assert len(active) == 3
        assert result["archived"] == 0

    @pytest.mark.asyncio
    async def test_returns_summary(self, db):
        result = await db.deduplicate_super_ideas()
        assert "kept" in result
        assert "archived" in result
        assert "groups" in result


class TestSuperIdeaGenerationDedup:
    """generate_seeded should skip when a similar super idea already exists."""

    @pytest.mark.asyncio
    async def test_seeded_skips_existing_base_name(self, db):
        """If a super idea with the same base name exists, skip generation."""
        from project_forge.engine.super_ideas import SuperIdeaGenerator

        # Pre-seed with enough ideas for clustering
        for i in range(20):
            cat = IdeaCategory.SECURITY_TOOL if i % 2 == 0 else IdeaCategory.VULNERABILITY_RESEARCH
            await db.save_idea(Idea(
                name=f"Idea {i}", tagline=f"Tagline {i}",
                description="Description.", category=cat,
                market_analysis="Market.", feasibility_score=0.8,
                mvp_scope="MVP.", tech_stack=["python"],
            ))

        # Pre-seed a super idea that would match
        await db.save_idea(_super("[SUPER] Autonomous Security Testing Platform"))

        gen = SuperIdeaGenerator(db)
        await gen.generate_seeded(slot=2)  # Attack & Defense slot

        # Should skip because base name already exists
        supers = [i for i in await db.list_ideas(limit=200) if i.name.startswith("[SUPER]")]
        base_names = [re.sub(r'\s*\([^)]+\)\s*$', '', n.name.replace("[SUPER] ", "")) for n in supers]
        # No new variant of "Autonomous Security Testing Platform"
        count = sum(1 for b in base_names if b == "Autonomous Security Testing Platform")
        assert count == 1, f"Expected 1 'Autonomous Security Testing Platform', got {count}"
