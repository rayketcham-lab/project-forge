"""TDD: Generation telemetry — read-only analytics over filtered_ideas + ideas.

Phase 1 (issue #54) provides the eyes for SI generation mode (#55):
- filter_rate_by_category: where dedup is rejecting most generations
- saturation_per_concept: which keywords are oversaturated
- novelty_trend: is the engine getting more or less novel over time
- diversity_lever_usage: how often each diversity prompt fires
- coverage_gaps: categories with low active idea counts

Each function is pure, takes a Database, returns a structured value.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from project_forge.models import FilteredIdea, Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, *, category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
          score: float = 0.82, days_ago: int = 0) -> Idea:
    return Idea(
        name=name,
        tagline=f"tag for {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
        generated_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


def _filtered(name: str, *, category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
              reason: str = "duplicate:tagline_similarity:0.85",
              days_ago: int = 0) -> FilteredIdea:
    fi = FilteredIdea(
        idea_name=name,
        idea_tagline=f"tag for {name}",
        idea_category=category,
        filter_reason=reason,
        original_idea_json="{}",
    )
    # FilteredIdea sets filtered_at automatically; override for time-window tests
    fi.filtered_at = datetime.now(UTC) - timedelta(days=days_ago)
    return fi


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "telemetry.db")
    await d.connect()
    yield d
    await d.close()


# ── filter_rate_by_category ───────────────────────────────────────────


class TestFilterRateByCategory:
    @pytest.mark.asyncio
    async def test_returns_per_category_rate(self, db):
        from project_forge.engine.telemetry import filter_rate_by_category

        # 1 accepted + 3 rejected in security-tool → rate = 0.75
        await db.save_idea(_idea("Accepted A", category=IdeaCategory.SECURITY_TOOL))
        for n in ("Reject 1", "Reject 2", "Reject 3"):
            await db.save_filtered_idea(_filtered(n, category=IdeaCategory.SECURITY_TOOL))

        # 1 accepted + 0 rejected in privacy → rate = 0.0
        await db.save_idea(_idea("Privacy Accepted", category=IdeaCategory.PRIVACY))

        rates = await filter_rate_by_category(db, days=7)

        assert rates[IdeaCategory.SECURITY_TOOL] == pytest.approx(0.75, abs=0.01)
        assert rates[IdeaCategory.PRIVACY] == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_respects_time_window(self, db):
        from project_forge.engine.telemetry import filter_rate_by_category

        await db.save_idea(_idea("Recent Accept", days_ago=1))
        await db.save_filtered_idea(_filtered("Old Reject", days_ago=30))

        rates = await filter_rate_by_category(db, days=7)
        # Old reject excluded → rate = 0.0 for security-tool (only recent accept)
        assert rates.get(IdeaCategory.SECURITY_TOOL, 0.0) == pytest.approx(0.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_dict(self, db):
        from project_forge.engine.telemetry import filter_rate_by_category

        rates = await filter_rate_by_category(db, days=7)
        assert rates == {}


# ── saturation_per_concept ────────────────────────────────────────────


class TestSaturationPerConcept:
    @pytest.mark.asyncio
    async def test_ranks_concepts_by_filter_count(self, db):
        from project_forge.engine.telemetry import saturation_per_concept

        # "certificate" appears in 4 rejections, "quantum" in 2, "fuzzing" in 1
        for n in ("Certificate Pinning A", "Certificate Validator B",
                 "Certificate Monitor C", "Certificate Watcher D"):
            await db.save_filtered_idea(_filtered(n))
        for n in ("Quantum Tool A", "Quantum Tool B"):
            await db.save_filtered_idea(_filtered(n))
        await db.save_filtered_idea(_filtered("Fuzzing Engine"))

        ranking = await saturation_per_concept(db, days=30, top_n=5)

        # Returns list of (concept, count) ranked descending
        assert isinstance(ranking, list)
        assert len(ranking) >= 2
        names = [c for c, _ in ranking]
        assert "certificate" in names
        # Certificate must outrank quantum
        cert_idx = names.index("certificate")
        if "quantum" in names:
            assert cert_idx < names.index("quantum")

    @pytest.mark.asyncio
    async def test_filters_stop_words(self, db):
        from project_forge.engine.telemetry import saturation_per_concept

        # "the" should not appear even if frequent
        for n in ("the Tool A", "the Tool B", "the Tool C"):
            await db.save_filtered_idea(_filtered(n))

        ranking = await saturation_per_concept(db, days=30, top_n=10)
        names = {c for c, _ in ranking}
        assert "the" not in names
        assert "tool" not in names  # "tool" is too generic

    @pytest.mark.asyncio
    async def test_respects_top_n(self, db):
        from project_forge.engine.telemetry import saturation_per_concept

        for i in range(20):
            await db.save_filtered_idea(_filtered(f"Concept{i}_word "))

        ranking = await saturation_per_concept(db, days=30, top_n=3)
        assert len(ranking) <= 3


# ── novelty_trend ─────────────────────────────────────────────────────


class TestNoveltyTrend:
    @pytest.mark.asyncio
    async def test_returns_per_day_avg_similarity(self, db):
        from project_forge.engine.telemetry import novelty_trend

        # Today: 2 high-similarity rejections (tagline_similarity:0.92, 0.88)
        await db.save_filtered_idea(
            _filtered("A", reason="duplicate:tagline_similarity:0.92 (similar to xyz)", days_ago=0),
        )
        await db.save_filtered_idea(
            _filtered("B", reason="duplicate:tagline_similarity:0.88 (similar to xyz)", days_ago=0),
        )

        trend = await novelty_trend(db, days=7)

        assert isinstance(trend, list)
        # Each entry: (date_str, avg_similarity)
        assert all(len(entry) == 2 for entry in trend)
        # Loose check — just verify structure is right
        assert len(trend) >= 1

    @pytest.mark.asyncio
    async def test_ignores_non_similarity_filters(self, db):
        from project_forge.engine.telemetry import novelty_trend

        # content_hash duplicates have no similarity score
        await db.save_filtered_idea(_filtered("Hash Dup", reason="duplicate:content_hash"))

        trend = await novelty_trend(db, days=7)
        # Should not crash; may be empty list
        assert isinstance(trend, list)


# ── diversity_lever_usage ─────────────────────────────────────────────


class TestDiversityLeverUsage:
    """Tracks how often contrarian/combinatoric/static diversity prompts fire.

    Source: generation_runs table doesn't currently track diversity mode.
    For Phase 1, we expose the function but it returns a default shape until
    Phase 2 wires generation_runs to track this. Test the SHAPE only.
    """

    @pytest.mark.asyncio
    async def test_returns_dict_with_known_keys(self, db):
        from project_forge.engine.telemetry import diversity_lever_usage

        usage = await diversity_lever_usage(db, days=7)

        assert isinstance(usage, dict)
        # Expected keys exist (values may be 0 until cron tracks them)
        assert "contrarian" in usage
        assert "combinatoric" in usage
        assert "static" in usage


# ── coverage_gaps ─────────────────────────────────────────────────────


class TestCoverageGaps:
    @pytest.mark.asyncio
    async def test_returns_underused_categories(self, db):
        from project_forge.engine.telemetry import coverage_gaps

        # Heavily populate security-tool (10), leave others empty
        for i in range(10):
            await db.save_idea(_idea(f"ST {i}", category=IdeaCategory.SECURITY_TOOL))

        gaps = await coverage_gaps(db, threshold=5)

        assert isinstance(gaps, list)
        # SECURITY_TOOL has 10, must NOT be in gaps
        assert IdeaCategory.SECURITY_TOOL not in gaps
        # PRIVACY has 0, must be in gaps
        assert IdeaCategory.PRIVACY in gaps

    @pytest.mark.asyncio
    async def test_empty_db_all_categories_gaps(self, db):
        from project_forge.engine.telemetry import coverage_gaps

        gaps = await coverage_gaps(db, threshold=5)
        # All 13 categories should be flagged when DB empty
        assert len(gaps) == len(list(IdeaCategory))
