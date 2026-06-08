"""Tests for the auto-promote (money-flipper) cadence.

Weekly job. Picks the highest-fundability idea among the money-friendly
categories that hasn't been auto-promoted yet, flips it to 'approved',
creates a GitHub issue with the full MVP spec, and stamps the idea so
subsequent runs skip it. Closes the gap between "engine generates ideas"
and "engine ships something with revenue potential."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, **overrides) -> Idea:
    base = dict(
        name=name,
        tagline=f"tag {name}",
        description="A solid idea description.",
        category=IdeaCategory.AUTOMATION_INCOME,
        market_analysis="Indie hackers with budget.",
        feasibility_score=0.8,
        mvp_scope="Phase 1: ship. Phase 2: monetize.",
        tech_stack=["python", "fastapi", "stripe"],
    )
    base.update(overrides)
    return Idea(**base)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "promote.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# Picker                                                                      #
# --------------------------------------------------------------------------- #


class TestPicker:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_eligible_ideas(self, db):
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        result = await pick_promotion_candidate(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_picks_highest_fundability(self, db):
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        low = _idea("Low", fundability_score=0.4)
        mid = _idea("Mid", fundability_score=0.65)
        top = _idea("Top", fundability_score=0.88)
        for i in (low, mid, top):
            await db.save_idea(i)

        result = await pick_promotion_candidate(db)
        assert result is not None
        assert result.name == "Top"

    @pytest.mark.asyncio
    async def test_skips_already_promoted(self, db):
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        already = _idea("Already", fundability_score=0.99)
        already.auto_promoted_at = datetime.now(UTC) - timedelta(days=2)
        fresh = _idea("Fresh", fundability_score=0.70)
        await db.save_idea(already)
        await db.save_idea(fresh)

        result = await pick_promotion_candidate(db)
        assert result is not None
        assert result.name == "Fresh"

    @pytest.mark.asyncio
    async def test_skips_low_fundability_below_threshold(self, db):
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        # All three are below the default threshold (0.55) — none picked.
        for n, s in (("a", 0.30), ("b", 0.45), ("c", 0.50)):
            await db.save_idea(_idea(n, fundability_score=s))

        result = await pick_promotion_candidate(db)
        assert result is None

    @pytest.mark.asyncio
    async def test_restricted_to_money_categories(self, db):
        """SECURITY_TOOL is a great category but not a money-flipper target.
        Even if its fundability is highest, the picker skips it."""
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        await db.save_idea(_idea(
            "Big-Sec",
            category=IdeaCategory.SECURITY_TOOL,
            fundability_score=0.99,
        ))
        await db.save_idea(_idea("Smaller-Money", fundability_score=0.6))

        result = await pick_promotion_candidate(db)
        assert result is not None
        assert result.name == "Smaller-Money"

    @pytest.mark.asyncio
    async def test_skips_archived_and_rejected(self, db):
        from project_forge.cron.auto_promote_runner import pick_promotion_candidate

        dead = _idea("Dead", fundability_score=0.99)
        await db.save_idea(dead)
        await db.update_idea_status(dead.id, "archived")

        live = _idea("Live", fundability_score=0.60)
        await db.save_idea(live)

        result = await pick_promotion_candidate(db)
        assert result is not None
        assert result.name == "Live"


# --------------------------------------------------------------------------- #
# Cycle execution                                                             #
# --------------------------------------------------------------------------- #


class TestRunCycle:
    @pytest.mark.asyncio
    async def test_no_candidate_returns_zero(self, db):
        from project_forge.cron.auto_promote_runner import run_auto_promote_cycle

        result = await run_auto_promote_cycle(db)
        assert result == {"promoted": 0, "idea_id": None, "issue_url": None}

    @pytest.mark.asyncio
    async def test_flips_status_and_stamps_timestamp(self, db):
        from project_forge.cron import auto_promote_runner

        idea = _idea("Money Idea", fundability_score=0.85)
        await db.save_idea(idea)

        with patch.object(
            auto_promote_runner, "_create_promotion_issue",
            return_value="https://github.com/x/y/issues/42",
        ):
            result = await auto_promote_runner.run_auto_promote_cycle(db)

        assert result["promoted"] == 1
        assert result["idea_id"] == idea.id

        loaded = await db.get_idea(idea.id)
        assert loaded.status == "approved"
        assert loaded.auto_promoted_at is not None
        assert loaded.github_issue_url == "https://github.com/x/y/issues/42"

    @pytest.mark.asyncio
    async def test_idempotent_second_run_picks_different_idea(self, db):
        from project_forge.cron import auto_promote_runner

        a = _idea("First", fundability_score=0.85)
        b = _idea("Second", fundability_score=0.80)
        await db.save_idea(a)
        await db.save_idea(b)

        with patch.object(
            auto_promote_runner, "_create_promotion_issue",
            return_value="https://github.com/x/y/issues/1",
        ):
            r1 = await auto_promote_runner.run_auto_promote_cycle(db)
            r2 = await auto_promote_runner.run_auto_promote_cycle(db)

        assert r1["idea_id"] == a.id
        assert r2["idea_id"] == b.id

    @pytest.mark.asyncio
    async def test_github_failure_does_not_promote(self, db):
        """If issue creation fails, the idea stays 'new' so the next cycle
        retries. Half-promoted state is worse than no promotion."""
        from project_forge.cron import auto_promote_runner

        idea = _idea("Money Idea", fundability_score=0.85)
        await db.save_idea(idea)

        with patch.object(
            auto_promote_runner, "_create_promotion_issue",
            side_effect=RuntimeError("gh down"),
        ):
            result = await auto_promote_runner.run_auto_promote_cycle(db)

        assert result["promoted"] == 0
        loaded = await db.get_idea(idea.id)
        assert loaded.status == "new"
        assert loaded.auto_promoted_at is None


# --------------------------------------------------------------------------- #
# Issue body                                                                  #
# --------------------------------------------------------------------------- #


class TestIssueBody:
    def test_body_includes_mvp_and_fundability(self):
        from project_forge.cron.auto_promote_runner import build_issue_body

        idea = _idea("Stripe Tool", fundability_score=0.82)
        body = build_issue_body(idea)
        assert "Stripe Tool" in body
        assert "0.82" in body
        assert "Phase 1" in body  # mvp_scope content
        assert "fundability" in body.lower()
