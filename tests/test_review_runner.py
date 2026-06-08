"""Tests for `project_forge.cron.review_runner` — fix #49.

The review runner ages-out + heuristically reviews ideas. Previously
covered only by integration paths via test_review_cycle.py and
test_review_no_api_key.py — the public functions had no targeted unit
tests. This file pins those down.

Public surface:
  - heuristic_review(idea, category_counts, total_ideas) -> dict
  - build_review_prompt(idea) -> str
  - run_review_cycle(db, batch_size, min_age_days) -> dict
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.cron.review_runner import (
    build_review_prompt,
    heuristic_review,
    run_review_cycle,
)
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str = "Test", **overrides) -> Idea:
    base = dict(
        name=name,
        tagline="t",
        description="A description that is long enough to read as substantial.",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="m",
        feasibility_score=0.7,
        mvp_scope="mvp" * 5,
        tech_stack=["python", "fastapi"],
    )
    base.update(overrides)
    return Idea(**base)


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "review.db")
    await d.connect()
    yield d
    await d.close()


# --------------------------------------------------------------------------- #
# heuristic_review                                                             #
# --------------------------------------------------------------------------- #


class TestHeuristicReview:
    def test_returns_required_keys(self):
        result = heuristic_review(_idea(), {"observability": 1}, 1)
        for k in ("verdict", "confidence", "reasoning", "suggestions"):
            assert k in result, f"missing key: {k}"

    def test_verdict_is_known_value(self):
        result = heuristic_review(_idea(), {}, 0)
        assert result["verdict"] in {"keep", "kill", "archive", "strengthen", "no_change"}

    def test_high_feasibility_does_not_kill(self):
        idea = _idea(feasibility_score=0.95)
        result = heuristic_review(idea, {"observability": 1}, 1)
        assert result["verdict"] not in ("kill", "archive")

    def test_low_feasibility_is_unfavoured(self):
        """A 0.1-feasibility idea in a saturated category should not be a
        clear keep — at minimum it carries some negative signal."""
        idea = _idea(feasibility_score=0.1)
        result = heuristic_review(
            idea,
            {"observability": 100},
            1000,
        )
        # Either it gets killed/archived, OR confidence in 'keep' is low.
        if result["verdict"] in ("keep", "strengthen", "no_change"):
            assert result["confidence"] <= 0.5

    def test_confidence_is_in_unit_interval(self):
        result = heuristic_review(_idea(), {}, 0)
        assert 0.0 <= result["confidence"] <= 1.0


# --------------------------------------------------------------------------- #
# build_review_prompt                                                          #
# --------------------------------------------------------------------------- #


class TestBuildReviewPrompt:
    def test_includes_idea_name(self):
        idea = _idea(name="Distinctive Idea Name Xyzzy")
        prompt = build_review_prompt(idea)
        assert "Distinctive Idea Name Xyzzy" in prompt

    def test_includes_category(self):
        idea = _idea(category=IdeaCategory.PQC_CRYPTOGRAPHY)
        prompt = build_review_prompt(idea)
        assert "pqc-cryptography" in prompt or "PQC" in prompt

    def test_prompt_is_substantial(self):
        prompt = build_review_prompt(_idea())
        assert len(prompt) > 200, "prompt should give the LLM real context"

    def test_includes_feasibility_signal(self):
        idea = _idea(feasibility_score=0.42)
        prompt = build_review_prompt(idea)
        assert "0.42" in prompt or "42" in prompt


# --------------------------------------------------------------------------- #
# run_review_cycle                                                             #
# --------------------------------------------------------------------------- #


class TestRunReviewCycle:
    @pytest.mark.asyncio
    async def test_empty_db_returns_zero(self, db):
        result = await run_review_cycle(db, batch_size=5, min_age_days=0)
        assert result["reviewed"] == 0
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_reviews_eligible_ideas(self, db):
        """An idea that's old enough + has no review yet should be reviewed."""
        from datetime import UTC, datetime, timedelta

        idea = _idea(name="Old enough")
        # Force generated_at far in the past so the min_age_days gate passes.
        idea.generated_at = datetime.now(UTC) - timedelta(days=14)
        await db.save_idea(idea)

        # Use heuristic path — no LLM call.
        with patch(
            "project_forge.cron.review_runner._get_api_key",
            return_value="",
        ):
            result = await run_review_cycle(db, batch_size=5, min_age_days=7)
        assert result["reviewed"] == 1
        assert result["results"][0]["status"] == "reviewed"

    @pytest.mark.asyncio
    async def test_min_age_days_gates_RE_review(self, db):
        """`min_age_days` controls the gap between successive reviews, NOT
        whether a fresh-but-unreviewed idea gets its FIRST review. An idea
        reviewed yesterday with min_age_days=7 should NOT be re-reviewed."""
        from datetime import UTC, datetime, timedelta

        idea = _idea(name="Reviewed recently")
        idea.generated_at = datetime.now(UTC) - timedelta(days=14)
        await db.save_idea(idea)
        # Insert a recent review row directly so its reviewed_at is fresh.
        await db.record_review(
            idea_id=idea.id,
            verdict="keep",
            confidence=0.6,
            reasoning="r",
            suggestions=[],
        )
        result = await run_review_cycle(db, batch_size=5, min_age_days=7)
        assert result["reviewed"] == 0

    @pytest.mark.asyncio
    async def test_already_reviewed_ideas_skipped(self, db):
        """An idea with a recent review row shouldn't be re-reviewed."""
        from datetime import UTC, datetime, timedelta

        idea = _idea(name="Already")
        idea.generated_at = datetime.now(UTC) - timedelta(days=14)
        await db.save_idea(idea)
        await db.record_review(
            idea_id=idea.id,
            verdict="keep",
            confidence=0.7,
            reasoning="r",
            suggestions=[],
        )
        with patch(
            "project_forge.cron.review_runner._get_api_key",
            return_value="",
        ):
            result = await run_review_cycle(db, batch_size=5, min_age_days=7)
        assert result["reviewed"] == 0
