"""TDD: Super idea display dedup and generation-time prevention.

Problem: Dashboard shows duplicate super ideas that are the same base concept
with different perspective suffixes:
  - "[SUPER] Autonomous Security Testing Platform"
  - "[SUPER] Autonomous Security Testing Platform (Attack & Defense)"
  - "[SUPER] Autonomous Security Testing Platform (Attack & Defense)"  (different category)

Extended problem (synthesis suffixes + hyphens):
  - "[SUPER] Well Known Defense Suite"
  - "[SUPER] Well Known Operations Center"   ← same base concept, different suffix
  - "[SUPER] Data-Cardinality Operations Center"  ← hyphen in name
  Stats card counts raw rows; list_super_ideas() deduplicates → mismatch.

Fix targets:
- _super_base_name: also strip synthesis suffixes + normalize hyphens
- list_super_ideas: use _super_base_name for consistent dedup
- get_stats: count deduped super ideas (len(list_super_ideas())) not raw rows
- _dynamic_cluster_name: remove hyphen template — names must use spaces
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
        await db.save_idea(_super("[SUPER] Threat Engine (Attack & Defense)", score=0.95))
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
        await db.db.execute("UPDATE ideas SET status = 'archived' WHERE id = ?", (high.id,))
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
            await db.save_idea(_super(f"[SUPER] Project {i} (Variant)", score=0.79 + i * 0.02))

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
        await db.save_idea(_super("[SUPER] Threat Engine", category=IdeaCategory.SECURITY_TOOL))

        candidate = _super(
            "[SUPER] Threat Engine (Attack & Defense)",
            category=IdeaCategory.VULNERABILITY_RESEARCH,
        )
        accepted, reason = await should_accept(candidate, db)

        assert not accepted

    @pytest.mark.asyncio
    async def test_rejects_same_keywords_different_synthesis_suffix(self, db):
        """'Well Known Defense Suite' and 'Well Known Operations Center' are the same concept."""
        await db.save_idea(_super("[SUPER] Well Known Defense Suite"))

        candidate = _super("[SUPER] Well Known Operations Center")
        accepted, reason = await should_accept(candidate, db)

        assert not accepted, "Same base keywords with different synthesis suffix must be rejected"
        assert reason is not None
        assert "duplicate" in reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_hyphenated_and_space_variant_as_same(self, db):
        """'Data-Cardinality Operations Center' and 'Data Cardinality Defense Suite' are the same."""
        await db.save_idea(_super("[SUPER] Data-Cardinality Operations Center"))

        candidate = _super("[SUPER] Data Cardinality Defense Suite")
        accepted, reason = await should_accept(candidate, db)

        assert not accepted, "Hyphen vs space variant of same keywords must be rejected"


class TestListSuperIdeasSynthesisSuffixDedup:
    """list_super_ideas must deduplicate by keyword base, not just parenthetical suffix."""

    @pytest.mark.asyncio
    async def test_same_keywords_different_synthesis_suffix_returns_one(self, db):
        """'Well Known Defense Suite' vs 'Well Known Operations Center' → only one shown."""
        await db.save_idea(_super("[SUPER] Well Known Defense Suite", score=0.91))
        await db.save_idea(_super("[SUPER] Well Known Operations Center", score=0.92))

        result = await db.list_super_ideas()
        assert len(result) == 1, f"Expected 1, got {len(result)}: {[r.name for r in result]}"
        assert result[0].feasibility_score == 0.92

    @pytest.mark.asyncio
    async def test_hyphenated_name_deduped_with_space_variant(self, db):
        """'Data-Cardinality Defense Suite' and 'Data Cardinality Operations Center' → one."""
        await db.save_idea(_super("[SUPER] Data-Cardinality Defense Suite", score=0.90))
        await db.save_idea(_super("[SUPER] Data Cardinality Operations Center", score=0.91))

        result = await db.list_super_ideas()
        assert len(result) == 1
        assert result[0].feasibility_score == 0.91

    @pytest.mark.asyncio
    async def test_genuinely_different_keywords_both_shown(self, db):
        """'Well Known Defense Suite' and 'Certificate Pinning Observatory' are different."""
        await db.save_idea(_super("[SUPER] Well Known Defense Suite"))
        await db.save_idea(_super("[SUPER] Certificate Pinning Observatory"))

        result = await db.list_super_ideas()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_ampersand_variant_deduplicated(self, db):
        """'Multi & Control Defense Suite' and 'Multi Control Command Center' → one."""
        await db.save_idea(_super("[SUPER] Multi & Control Defense Suite", score=0.92))
        await db.save_idea(_super("[SUPER] Multi Control Command Center", score=0.91))

        result = await db.list_super_ideas()
        assert len(result) == 1
        assert result[0].feasibility_score == 0.92


class TestSuperBaseNameExtraction:
    """_super_base_name must strip synthesis suffixes and normalize hyphens."""

    def test_strips_parenthetical_suffix(self):
        from project_forge.engine.dedup import _super_base_name

        assert _super_base_name("[SUPER] Threat Engine (Attack & Defense)") == "threat engine"

    def test_strips_synthesis_suffix_operations_center(self):
        from project_forge.engine.dedup import _super_base_name

        assert _super_base_name("[SUPER] Well Known Operations Center") == "well known"

    def test_strips_synthesis_suffix_defense_suite(self):
        from project_forge.engine.dedup import _super_base_name

        assert _super_base_name("[SUPER] Well Known Defense Suite") == "well known"

    def test_normalizes_hyphen_to_space(self):
        from project_forge.engine.dedup import _super_base_name

        result = _super_base_name("[SUPER] Data-Cardinality Operations Center")
        assert result == "data cardinality"
        assert "-" not in result

    def test_strips_observatory_suffix(self):
        from project_forge.engine.dedup import _super_base_name

        assert _super_base_name("[SUPER] Certificate-Pinning Observatory") == "certificate pinning"

    def test_simple_name_unchanged(self):
        from project_forge.engine.dedup import _super_base_name

        result = _super_base_name("[SUPER] Threat Engine")
        assert result == "threat engine"

    def test_normalizes_ampersand_to_space(self):
        from project_forge.engine.dedup import _super_base_name

        result = _super_base_name("[SUPER] Multi & Control Defense Suite")
        assert result == "multi control"
        assert "&" not in result

    def test_ampersand_variant_equals_space_variant(self):
        from project_forge.engine.dedup import _super_base_name

        with_amp = _super_base_name("[SUPER] Certificate & Pinning Observatory")
        without_amp = _super_base_name("[SUPER] Certificate Pinning Observatory")
        assert with_amp == without_amp


class TestStatsCountMatchesDisplay:
    """stats.super_ideas must equal len(list_super_ideas()) — no raw vs deduped mismatch."""

    @pytest.mark.asyncio
    async def test_stats_count_matches_deduped_display(self, db):
        """When variants exist, stats count must match the deduped display count."""
        await db.save_idea(_super("[SUPER] Well Known Defense Suite", score=0.91))
        await db.save_idea(_super("[SUPER] Well Known Operations Center", score=0.92))
        await db.save_idea(_super("[SUPER] Certificate Pinning Observatory", score=0.89))

        stats = await db.get_stats()
        displayed = await db.list_super_ideas()

        assert stats["super_ideas"] == len(displayed), (
            f"Stats shows {stats['super_ideas']} but display shows {len(displayed)} — mismatch"
        )

    @pytest.mark.asyncio
    async def test_stats_count_no_variants(self, db):
        """Without variants, stats and display count must agree."""
        await db.save_idea(_super("[SUPER] Threat Engine"))
        await db.save_idea(_super("[SUPER] Privacy Platform"))

        stats = await db.get_stats()
        displayed = await db.list_super_ideas()

        assert stats["super_ideas"] == len(displayed) == 2


class TestSuperIdeaNameNoHyphens:
    """_dynamic_cluster_name must never return hyphenated names."""

    def test_no_hyphens_in_generated_names(self):
        """Run many name generations — none should contain a hyphen from the template."""
        from project_forge.engine.super_ideas import _dynamic_cluster_name
        from project_forge.models import Idea, IdeaCategory

        def _dummy_idea(name: str) -> Idea:
            return Idea(
                name=name,
                tagline="certificate pinning: healthcare",
                description="Desc.",
                category=IdeaCategory.SECURITY_TOOL,
                market_analysis="Market.",
                feasibility_score=0.8,
                mvp_scope="MVP.",
                tech_stack=["python"],
            )

        ideas = [
            _dummy_idea("Certificate Pinning Tool"),
            _dummy_idea("Data Cardinality Scanner"),
            _dummy_idea("Well Known Security Scanner"),
        ]
        categories = frozenset({IdeaCategory.SECURITY_TOOL, IdeaCategory.CRYPTO_INFRASTRUCTURE})

        # Run 50 times to cover random.choice
        hyphenated = []
        for _ in range(50):
            name = _dynamic_cluster_name(ideas, categories)
            # Strip the [SUPER] prefix if present and check for hyphens in keyword part
            core = name.replace("[SUPER] ", "")
            # A hyphen between two title-cased words is the bad pattern
            import re

            if re.search(r"[A-Z][a-z]+-[A-Z][a-z]+", core):
                hyphenated.append(name)

        assert hyphenated == [], f"_dynamic_cluster_name produced hyphenated names: {hyphenated}"
