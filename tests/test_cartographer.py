"""Tests for engine/cartographer.py — State of the Forge atlas.

Pattern: Database(tmp_path / 'x.db') async fixture, no network, no real LLM.
Seed ideas directly via db.save_idea(); seed filtered_ideas via direct SQL
to exercise the rejection-rate saturation path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "cartographer.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _make_idea(category: IdeaCategory, name_suffix: str = "") -> Idea:
    """Construct a minimal valid Idea in the given category."""
    return Idea(
        name=f"{category.value} idea {name_suffix}",
        tagline=f"tagline for {category.value} {name_suffix}",
        description="A detailed description " * 5,
        category=category,
        market_analysis="Some market analysis.",
        feasibility_score=0.6,
        mvp_scope="Phase 1 scope.",
        tech_stack=["python"],
        content_hash=f"{category.value}-{name_suffix}-{uuid.uuid4().hex[:8]}",
    )


async def _seed_category(db: Database, category: IdeaCategory, n: int) -> None:
    """Insert n active ideas into category."""
    for i in range(n):
        await db.save_idea(_make_idea(category, str(i)))


async def _seed_filtered(
    db: Database,
    category: IdeaCategory,
    n: int,
    reason: str = "duplicate:tagline_similarity:0.92",
) -> None:
    """Insert n filtered_ideas rows for the given category (direct SQL)."""
    now = datetime.now(UTC).isoformat()
    for i in range(n):
        await db.db.execute(
            "INSERT INTO filtered_ideas "
            "(id, idea_name, idea_tagline, idea_category, filter_reason, "
            "original_idea_json, filtered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                f"filtered-{category.value}-{i}",
                "filtered tagline",
                category.value,
                reason,
                "{}",
                now,
            ),
        )
    await db.db.commit()


# --------------------------------------------------------------------------- #
# build_atlas: white_space detection                                           #
# --------------------------------------------------------------------------- #


class TestBuildAtlasWhiteSpace:
    @pytest.mark.asyncio
    async def test_empty_db_all_categories_are_white_space(self, db):
        from project_forge.engine.cartographer import WHITE_SPACE_THRESHOLD, build_atlas

        atlas = await build_atlas(db)
        # With no ideas, every category should be white space.
        assert len(atlas["white_space"]) == len(IdeaCategory)
        # Every count should be zero.
        assert all(v == 0 for v in atlas["vertical_coverage"].values())
        # Threshold constant accessible for the template.
        assert WHITE_SPACE_THRESHOLD >= 1

    @pytest.mark.asyncio
    async def test_low_count_category_flagged_white_space(self, db):
        from project_forge.engine.cartographer import WHITE_SPACE_THRESHOLD, build_atlas

        # Seed one category just below the threshold.
        below = IdeaCategory.PQC_CRYPTOGRAPHY
        await _seed_category(db, below, WHITE_SPACE_THRESHOLD - 1)

        # Seed one category at or above the threshold.
        above = IdeaCategory.SECURITY_TOOL
        await _seed_category(db, above, WHITE_SPACE_THRESHOLD)

        atlas = await build_atlas(db)
        assert below.value in atlas["white_space"]
        assert above.value not in atlas["white_space"]

    @pytest.mark.asyncio
    async def test_exactly_at_threshold_not_white_space(self, db):
        from project_forge.engine.cartographer import WHITE_SPACE_THRESHOLD, build_atlas

        cat = IdeaCategory.COMPLIANCE
        await _seed_category(db, cat, WHITE_SPACE_THRESHOLD)

        atlas = await build_atlas(db)
        assert cat.value not in atlas["white_space"]

    @pytest.mark.asyncio
    async def test_rejected_ideas_not_counted_as_active(self, db):
        from project_forge.engine.cartographer import build_atlas

        cat = IdeaCategory.PRIVACY
        # Save ideas then mark them rejected.
        for i in range(10):
            idea = _make_idea(cat, f"rejected-{i}")
            idea.status = "rejected"
            await db.save_idea(idea)

        atlas = await build_atlas(db)
        # Rejected ideas shouldn't count — category should still be white space.
        assert atlas["vertical_coverage"][cat.value] == 0
        assert cat.value in atlas["white_space"]


# --------------------------------------------------------------------------- #
# build_atlas: saturation detection                                            #
# --------------------------------------------------------------------------- #


class TestBuildAtlasSaturation:
    @pytest.mark.asyncio
    async def test_heavy_category_flagged_saturated(self, db):
        from project_forge.engine.cartographer import (
            SATURATION_COUNT_THRESHOLD,
            build_atlas,
        )

        heavy = IdeaCategory.AUTOMATION
        await _seed_category(db, heavy, SATURATION_COUNT_THRESHOLD)

        atlas = await build_atlas(db)
        assert heavy.value in atlas["saturation"]

    @pytest.mark.asyncio
    async def test_below_count_threshold_not_saturated_without_high_rate(self, db):
        from project_forge.engine.cartographer import (
            SATURATION_COUNT_THRESHOLD,
            build_atlas,
        )

        cat = IdeaCategory.DEVOPS_TOOLING
        await _seed_category(db, cat, SATURATION_COUNT_THRESHOLD - 1)

        atlas = await build_atlas(db)
        # No filtered ideas seeded, so rejection rate is 0 — should NOT be
        # flagged as saturated.
        assert cat.value not in atlas["saturation"]

    @pytest.mark.asyncio
    async def test_high_rejection_rate_also_flags_saturation(self, db):
        """A category with few active ideas but many rejections is saturated."""
        from project_forge.engine.cartographer import (
            SATURATION_RATE_THRESHOLD,
            build_atlas,
        )

        cat = IdeaCategory.OBSERVABILITY
        # A handful of active ideas (below the count threshold).
        await _seed_category(db, cat, 3)
        # But a lot of rejections (rejection rate >> threshold).
        reject_n = int(3 / (1 - SATURATION_RATE_THRESHOLD) * 2)
        await _seed_filtered(db, cat, reject_n)

        atlas = await build_atlas(db)
        assert cat.value in atlas["saturation"]


# --------------------------------------------------------------------------- #
# build_atlas: top_clusters + vertical_coverage                                #
# --------------------------------------------------------------------------- #


class TestBuildAtlasClusters:
    @pytest.mark.asyncio
    async def test_top_clusters_sorted_descending(self, db):
        from project_forge.engine.cartographer import TOP_CLUSTER_COUNT, build_atlas

        cats = [
            (IdeaCategory.SECURITY_TOOL, 15),
            (IdeaCategory.AUTOMATION, 10),
            (IdeaCategory.DEVOPS_TOOLING, 5),
        ]
        for cat, n in cats:
            await _seed_category(db, cat, n)

        atlas = await build_atlas(db)
        clusters = atlas["top_clusters"]
        assert len(clusters) <= TOP_CLUSTER_COUNT
        counts = [c["count"] for c in clusters]
        assert counts == sorted(counts, reverse=True)

    @pytest.mark.asyncio
    async def test_vertical_coverage_contains_all_categories(self, db):
        from project_forge.engine.cartographer import build_atlas

        atlas = await build_atlas(db)
        for cat in IdeaCategory:
            assert cat.value in atlas["vertical_coverage"]

    @pytest.mark.asyncio
    async def test_top_cluster_has_expected_keys(self, db):
        from project_forge.engine.cartographer import build_atlas

        await _seed_category(db, IdeaCategory.MARKET_GAP, 3)
        atlas = await build_atlas(db)
        for entry in atlas["top_clusters"]:
            assert "category" in entry
            assert "count" in entry
            assert isinstance(entry["count"], int)


# --------------------------------------------------------------------------- #
# build_atlas: recommended_next_bet                                            #
# --------------------------------------------------------------------------- #


class TestBuildAtlasRecommendation:
    @pytest.mark.asyncio
    async def test_recommended_bet_is_from_white_space(self, db):
        """The recommended_next_bet should be a white-space category."""
        from project_forge.engine.cartographer import build_atlas

        atlas = await build_atlas(db)
        rec = atlas["recommended_next_bet"]
        # Either 'balanced' (nothing to recommend) or a white-space entry.
        if rec != "balanced":
            assert rec in atlas["white_space"]

    @pytest.mark.asyncio
    async def test_balanced_when_all_categories_healthy(self, db):
        """If every category exceeds the threshold, recommend 'balanced'."""
        from project_forge.engine.cartographer import WHITE_SPACE_THRESHOLD, build_atlas

        # Fill every category to the threshold so nothing is white-space.
        for cat in IdeaCategory:
            await _seed_category(db, cat, WHITE_SPACE_THRESHOLD)

        atlas = await build_atlas(db)
        assert atlas["recommended_next_bet"] == "balanced"
        assert atlas["white_space"] == []

    @pytest.mark.asyncio
    async def test_self_improvement_not_recommended(self, db):
        """The self-improvement category should not be the recommended bet."""
        from project_forge.engine.cartographer import WHITE_SPACE_THRESHOLD, build_atlas

        # Make self-improvement the *only* non-white-space category so that
        # everything else is technically white-space.  The recommendation
        # engine must still skip it.
        await _seed_category(db, IdeaCategory.SELF_IMPROVEMENT, WHITE_SPACE_THRESHOLD)

        atlas = await build_atlas(db)
        rec = atlas["recommended_next_bet"]
        assert rec != IdeaCategory.SELF_IMPROVEMENT.value


# --------------------------------------------------------------------------- #
# format_memo                                                                  #
# --------------------------------------------------------------------------- #


class TestFormatMemo:
    def _sample_atlas(self) -> dict:
        return {
            "white_space": ["pqc-cryptography", "nist-standards"],
            "saturation": ["security-tool"],
            "top_clusters": [
                {"category": "security-tool", "count": 25},
                {"category": "automation", "count": 12},
            ],
            "vertical_coverage": {
                "pqc-cryptography": 2,
                "nist-standards": 0,
                "security-tool": 25,
                "automation": 12,
            },
            "recommended_next_bet": "pqc-cryptography",
            "generated_at": "2026-06-27T00:00:00+00:00",
        }

    def test_returns_non_empty_string(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert isinstance(memo, str)
        assert len(memo) > 100

    def test_memo_contains_white_space_categories(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert "pqc-cryptography" in memo
        assert "nist-standards" in memo

    def test_memo_contains_saturation_categories(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert "security-tool" in memo

    def test_memo_contains_recommendation(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert "pqc-cryptography" in memo
        assert "Recommended" in memo or "recommended" in memo

    def test_memo_contains_timestamp(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert "2026-06-27" in memo

    def test_memo_starts_with_heading(self):
        from project_forge.engine.cartographer import format_memo

        memo = format_memo(self._sample_atlas())
        assert memo.startswith("# State of the Forge")

    def test_balanced_recommendation(self):
        from project_forge.engine.cartographer import format_memo

        atlas = self._sample_atlas()
        atlas["white_space"] = []
        atlas["recommended_next_bet"] = "balanced"
        memo = format_memo(atlas)
        assert "Balanced" in memo or "balanced" in memo

    def test_empty_saturation(self):
        from project_forge.engine.cartographer import format_memo

        atlas = self._sample_atlas()
        atlas["saturation"] = []
        memo = format_memo(atlas)
        assert "No categories flagged as saturated" in memo


# --------------------------------------------------------------------------- #
# Full round-trip: seed → atlas → memo                                         #
# --------------------------------------------------------------------------- #


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_atlas_and_memo_from_seeded_db(self, db):
        """End-to-end: seed a realistic corpus, build atlas, format memo."""
        from project_forge.engine.cartographer import build_atlas, format_memo

        # Simulate a corpus with varied density.
        await _seed_category(db, IdeaCategory.SECURITY_TOOL, 22)  # saturated
        await _seed_category(db, IdeaCategory.AUTOMATION, 8)  # healthy
        await _seed_category(db, IdeaCategory.PQC_CRYPTOGRAPHY, 1)  # white space

        atlas = await build_atlas(db)

        assert IdeaCategory.SECURITY_TOOL.value in atlas["saturation"]
        assert IdeaCategory.PQC_CRYPTOGRAPHY.value in atlas["white_space"]
        assert IdeaCategory.AUTOMATION.value not in atlas["white_space"]
        assert IdeaCategory.AUTOMATION.value not in atlas["saturation"]

        memo = format_memo(atlas)
        assert "# State of the Forge" in memo
        assert IdeaCategory.PQC_CRYPTOGRAPHY.value in memo
        assert len(memo) > 200
