"""Tests for the gated Scoreboard auto-tune (v0.17).

The engine learns: realized outcome signals become small, clamped per-(axis,
category) score nudges that the heuristic scorers pick up. Safety-critical
invariant: an EMPTY cache (the default) means zero behaviour change.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine import scoreboard
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "autotune.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Never let learned nudges leak across tests (the cache is module-global)."""
    scoreboard._NUDGE_CACHE.clear()
    yield
    scoreboard._NUDGE_CACHE.clear()


def _idea(cat: IdeaCategory, **over) -> Idea:
    return Idea(
        name="x",
        tagline="t",
        description="d" * 40,
        category=cat,
        market_analysis="m" * 20,
        feasibility_score=0.5,
        mvp_scope="s" * 10,
        tech_stack=["python"],
        **over,
    )


async def _seed_fundability_signals(db: Database) -> None:
    # micro-saas: low predicted, high realized → under-rated → +nudge
    # security-tool: high predicted, low realized → over-rated → -nudge
    rows = [
        (IdeaCategory.MICRO_SAAS, 0.30, 30000.0, "m1"),
        (IdeaCategory.MICRO_SAAS, 0.35, 25000.0, "m2"),
        (IdeaCategory.SECURITY_TOOL, 0.90, 100.0, "s1"),
        (IdeaCategory.SECURITY_TOOL, 0.85, 200.0, "s2"),
    ]
    for cat, pred, val, ch in rows:
        idea = _idea(cat, content_hash=ch)
        await db.save_idea(idea)
        await scoreboard.record_signal(
            db,
            idea_id=idea.id,
            axis="fundability",
            predicted=pred,
            metric="oss_challenger_stars",
            value=val,
            entity_ref="x",
        )


class TestComputeNudges:
    @pytest.mark.asyncio
    async def test_rewards_underrated_penalizes_overrated(self, db):
        await _seed_fundability_signals(db)
        nudges = await scoreboard.compute_weight_nudges(db)
        by_cat = {n["category"]: n["nudge"] for n in nudges}
        assert by_cat.get("micro-saas") == 0.05
        assert by_cat.get("security-tool") == -0.05

    @pytest.mark.asyncio
    async def test_too_few_signals_yields_nothing(self, db):
        idea = _idea(IdeaCategory.MICRO_SAAS, content_hash="z")
        await db.save_idea(idea)
        await scoreboard.record_signal(
            db,
            idea_id=idea.id,
            axis="fundability",
            predicted=0.5,
            metric="m",
            value=1.0,
            entity_ref="x",
        )
        assert await scoreboard.compute_weight_nudges(db) == []


class TestApplyAndCache:
    @pytest.mark.asyncio
    async def test_apply_persists_and_caches(self, db):
        await _seed_fundability_signals(db)
        res = await scoreboard.apply_autotune(db)
        assert res["applied"] == 2
        assert scoreboard.learned_nudge("fundability", IdeaCategory.MICRO_SAAS) == 0.05
        # survives a fresh load from the table
        scoreboard._NUDGE_CACHE.clear()
        await scoreboard.load_nudges(db)
        assert scoreboard.learned_nudge("fundability", "security-tool") == -0.05

    @pytest.mark.asyncio
    async def test_default_cache_is_zero(self, db):
        assert scoreboard.learned_nudge("fundability", IdeaCategory.MICRO_SAAS) == 0.0


class TestScorerPickup:
    @pytest.mark.asyncio
    async def test_fundability_picks_up_the_learned_nudge(self, db):
        from project_forge.engine.fundability import score_fundability_heuristic

        idea = _idea(IdeaCategory.MICRO_SAAS, content_hash="pk")
        base = score_fundability_heuristic(idea)  # empty cache
        await _seed_fundability_signals(db)
        await scoreboard.apply_autotune(db)
        tuned = score_fundability_heuristic(idea)
        assert round(tuned - base, 4) == 0.05
