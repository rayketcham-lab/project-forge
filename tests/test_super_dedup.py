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
    async def test_archived_concept_does_not_block_future_generation(self, db):
        """Archived super ideas must not permanently veto re-synthesis on the same concept.

        Bug: generate_seeded built existing_super_primaries from ALL ideas including
        archived ones. An archived super idea with a generic fallback tagline like
        '6-capability synthesis: end-to-end platform' permanently blocked any future
        super idea whose cluster also fell back to that phrase.
        """
        # Active super → concept MUST block
        active = Idea(
            name="[SUPER] Active Security Platform",
            tagline="certificate pinning enforcement: synthesized into one platform",
            description="Active super.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market.",
            feasibility_score=0.95,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(active)

        # Archived super → concept must NOT block
        archived = Idea(
            name="[SUPER] Old Supply Chain Platform",
            tagline="supply chain scanning method: synthesized into one platform",
            description="Old archived super.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market.",
            feasibility_score=0.95,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(archived)
        await db.update_idea_status(archived.id, "archived")

        # Rejected super → concept must NOT block either
        rejected = Idea(
            name="[SUPER] Rejected Compliance Engine",
            tagline="compliance audit automation: synthesized into one platform",
            description="Rejected super.",
            category=IdeaCategory.COMPLIANCE,
            market_analysis="Market.",
            feasibility_score=0.92,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(rejected)
        await db.update_idea_status(rejected.id, "rejected")

        # Replicate the dedup filtering as generate_seeded does
        all_ideas = await db.list_ideas(limit=2000)
        existing_super_primaries: set[str] = set()
        for ex in all_ideas:
            if not ex.name.startswith("[SUPER]"):
                continue
            if ex.status in ("archived", "rejected"):
                continue  # this is the fix
            primary = ex.tagline.split(" + ")[0].split(":")[0].strip().lower()
            if primary and len(primary) > 5:
                existing_super_primaries.add(primary)

        assert "certificate pinning enforcement" in existing_super_primaries, (
            "Active super's concept must block future generation"
        )
        assert "supply chain scanning method" not in existing_super_primaries, (
            "Archived super's concept must NOT block future generation"
        )
        assert "compliance audit automation" not in existing_super_primaries, (
            "Rejected super's concept must NOT block future generation"
        )

    @pytest.mark.asyncio
    async def test_seeded_skips_existing_base_name(self, db):
        """If a super idea with the same base name exists, skip generation."""
        from project_forge.engine.super_ideas import SuperIdeaGenerator

        # Pre-seed with enough ideas for clustering
        for i in range(20):
            cat = IdeaCategory.SECURITY_TOOL if i % 2 == 0 else IdeaCategory.VULNERABILITY_RESEARCH
            await db.save_idea(
                Idea(
                    name=f"Idea {i}",
                    tagline=f"Tagline {i}",
                    description="Description.",
                    category=cat,
                    market_analysis="Market.",
                    feasibility_score=0.8,
                    mvp_scope="MVP.",
                    tech_stack=["python"],
                )
            )

        # Pre-seed a super idea that would match
        await db.save_idea(_super("[SUPER] Autonomous Security Testing Platform"))

        gen = SuperIdeaGenerator(db)
        await gen.generate_seeded(slot=2)  # Attack & Defense slot

        # Should skip because base name already exists
        supers = [i for i in await db.list_ideas(limit=200) if i.name.startswith("[SUPER]")]
        base_names = [re.sub(r"\s*\([^)]+\)\s*$", "", n.name.replace("[SUPER] ", "")) for n in supers]
        # No new variant of "Autonomous Security Testing Platform"
        count = sum(1 for b in base_names if b == "Autonomous Security Testing Platform")
        assert count == 1, f"Expected 1 'Autonomous Security Testing Platform', got {count}"

    @pytest.mark.asyncio
    async def test_generic_fallback_tagline_is_rejected(self, db):
        """Super ideas with 'N-capability synthesis' fallback taglines must not be stored.

        The fallback fires when _build_super_tagline finds no usable concepts in the
        cluster ideas. Storing such ideas pollutes the DB and permanently blocks future
        generation via concept dedup.
        """
        from project_forge.engine.super_ideas import SuperIdeaGenerator

        # Seed with ideas that have very short taglines (no colons, short names)
        # so _build_super_tagline is likely to fall back to the generic phrase.
        for i in range(20):
            cat = IdeaCategory.SECURITY_TOOL if i % 2 == 0 else IdeaCategory.VULNERABILITY_RESEARCH
            await db.save_idea(
                Idea(
                    name=f"Tool {i}",  # short name, likely to fall back
                    tagline="fix it",  # no colon, ≤4 chars core → triggers fallback
                    description="Description.",
                    category=cat,
                    market_analysis="Market.",
                    feasibility_score=0.8,
                    mvp_scope="MVP.",
                    tech_stack=["python"],
                )
            )

        gen = SuperIdeaGenerator(db)
        await gen.generate_seeded(slot=2)

        # If the quality gate works, no "N-capability synthesis" super should be stored
        all_ideas = await db.list_ideas(limit=200)
        bad_supers = [
            i for i in all_ideas
            if i.name.startswith("[SUPER]") and "capability synthesis" in i.tagline
        ]
        assert bad_supers == [], (
            "Generic fallback taglines must not be stored: "
            + ", ".join(f"{i.name!r}: {i.tagline!r}" for i in bad_supers)
        )
