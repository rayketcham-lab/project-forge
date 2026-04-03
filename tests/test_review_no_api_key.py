"""Tests for review runner working WITHOUT an API key.

Fixes #30: The review runner, SI runner, and challenge all fail without an API key,
violating the core promise: "Works without an API key."

The review runner must produce meaningful verdicts using heuristic analysis when
no API key is configured. Claude enhances when available, but is not required.
"""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_nokey.db")
    await d.connect()
    yield d
    await d.close()


def _idea(name: str, score=0.75, status="new", category=IdeaCategory.SECURITY_TOOL,
          generated_at=None, description="A solid project idea.", **kw) -> Idea:
    defaults = dict(
        name=name, tagline=f"Tagline for {name}",
        description=description, category=category,
        market_analysis="Market need.", feasibility_score=score,
        mvp_scope="Build it.", tech_stack=["python"], status=status,
    )
    if generated_at:
        defaults["generated_at"] = generated_at
    defaults.update(kw)
    return Idea(**defaults)


# ---------------------------------------------------------------------------
# 1. Heuristic review produces verdicts without API
# ---------------------------------------------------------------------------


class TestHeuristicReview:
    """heuristic_review returns a verdict dict using local signals only."""

    def test_returns_verdict_dict(self):
        from project_forge.cron.review_runner import heuristic_review

        idea = _idea("Test Idea", score=0.75)
        result = heuristic_review(idea, category_counts={}, total_ideas=100)

        assert "verdict" in result
        assert "confidence" in result
        assert "reasoning" in result
        assert "suggestions" in result
        assert result["verdict"] in ("keep", "strengthen", "pivot", "narrow",
                                      "expand", "archive", "kill")

    def test_low_score_suggests_archive(self):
        """Ideas with very low feasibility scores should lean toward archive."""
        from project_forge.cron.review_runner import heuristic_review

        idea = _idea("Weak Idea", score=0.3)
        result = heuristic_review(idea, category_counts={}, total_ideas=100)

        assert result["verdict"] in ("archive", "kill", "narrow")

    def test_old_idea_gets_flagged(self):
        """Ideas older than 30 days should get some staleness signal."""
        from project_forge.cron.review_runner import heuristic_review

        old = _idea("Ancient Idea", score=0.6,
                     generated_at=datetime(2025, 6, 1, tzinfo=UTC))
        result = heuristic_review(old, category_counts={}, total_ideas=100)

        # Old + mediocre score = should not be "keep"
        assert result["verdict"] != "keep" or result["confidence"] < 0.5

    def test_high_score_recent_keeps(self):
        """High-scoring recent ideas should be kept."""
        from project_forge.cron.review_runner import heuristic_review

        fresh = _idea("Great Idea", score=0.9,
                       generated_at=datetime(2026, 3, 30, tzinfo=UTC))
        result = heuristic_review(fresh, category_counts={}, total_ideas=100)

        assert result["verdict"] in ("keep", "strengthen", "expand")

    def test_saturated_category_narrows(self):
        """Ideas in oversaturated categories should lean toward narrow/archive."""
        from project_forge.cron.review_runner import heuristic_review

        idea = _idea("Another Security Tool", score=0.55,
                      category=IdeaCategory.SECURITY_TOOL,
                      generated_at=datetime(2026, 1, 15, tzinfo=UTC))
        counts = {"security-tool": 400, "automation": 50}
        result = heuristic_review(idea, category_counts=counts, total_ideas=500)

        # Heavily saturated + mediocre score
        assert result["verdict"] in ("narrow", "archive", "kill", "pivot")

    def test_short_description_weakens_verdict(self):
        """Ideas with very short descriptions signal low quality."""
        from project_forge.cron.review_runner import heuristic_review

        idea = _idea("Sparse Idea", score=0.7, description="Do the thing.")
        result = heuristic_review(idea, category_counts={}, total_ideas=100)

        assert result["confidence"] < 0.8  # Can't be very confident about thin ideas


# ---------------------------------------------------------------------------
# 2. Review cycle uses heuristic when no API key
# ---------------------------------------------------------------------------


class TestReviewCycleNoKey:
    """run_review_cycle should use heuristic review when no API key is set."""

    @pytest.mark.asyncio
    async def test_cycle_succeeds_without_api_key(self, db):
        """The review cycle should produce verdicts, not errors, without a key."""
        from project_forge.cron.review_runner import run_review_cycle

        await db.save_idea(_idea("Idea A"))
        await db.save_idea(_idea("Idea B"))

        # Ensure no API key is available
        env = {k: v for k, v in os.environ.items()
               if "ANTHROPIC" not in k and "FORGE_ANTHROPIC" not in k}

        with patch.dict(os.environ, env, clear=True), \
             patch("project_forge.cron.review_runner.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            result = await run_review_cycle(db, batch_size=5)

        assert result["reviewed"] == 2
        errors = [r for r in result["results"] if r["status"] == "error"]
        assert len(errors) == 0, f"Got errors without API key: {errors}"

    @pytest.mark.asyncio
    async def test_cycle_records_verdicts_without_key(self, db):
        """Heuristic verdicts should be recorded in the DB like API verdicts."""
        from project_forge.cron.review_runner import run_review_cycle

        idea = _idea("Recorded Idea")
        await db.save_idea(idea)

        env = {k: v for k, v in os.environ.items()
               if "ANTHROPIC" not in k and "FORGE_ANTHROPIC" not in k}

        with patch.dict(os.environ, env, clear=True), \
             patch("project_forge.cron.review_runner.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            await run_review_cycle(db, batch_size=5)

        reviews = await db.get_idea_reviews(idea.id)
        assert len(reviews) == 1
        assert reviews[0]["verdict"] in ("keep", "strengthen", "pivot", "narrow",
                                          "expand", "archive", "kill")

    @pytest.mark.asyncio
    async def test_cycle_auto_archives_heuristic_kills(self, db):
        """Heuristic kill verdicts should also trigger auto-archive."""
        from project_forge.cron.review_runner import run_review_cycle

        # Very old, very low score = likely kill
        doomed = _idea("Terrible Old Idea", score=0.1,
                        generated_at=datetime(2025, 1, 1, tzinfo=UTC),
                        description="x")
        await db.save_idea(doomed)

        env = {k: v for k, v in os.environ.items()
               if "ANTHROPIC" not in k and "FORGE_ANTHROPIC" not in k}

        with patch.dict(os.environ, env, clear=True), \
             patch("project_forge.cron.review_runner.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            await run_review_cycle(db, batch_size=5)

        updated = await db.get_idea(doomed.id)
        # Very old + terrible score should auto-archive
        assert updated.status == "archived"


# ---------------------------------------------------------------------------
# 3. SI runner graceful skip without key
# ---------------------------------------------------------------------------


class TestSIRunnerNoKey:
    """Self-improve runner should skip gracefully without an API key."""

    @pytest.mark.asyncio
    async def test_si_runner_skips_without_key(self):
        """run_self_improve_cycle should return cleanly, not crash."""
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        fake_issues = [{"number": 99, "title": "Test", "body": "Fix stuff",
                        "url": "http://example.com", "labels": [], "state": "OPEN"}]

        env = {k: v for k, v in os.environ.items()
               if "ANTHROPIC" not in k and "FORGE_ANTHROPIC" not in k}

        with patch.dict(os.environ, env, clear=True), \
             patch("project_forge.cron.self_improve_runner.settings") as mock_settings, \
             patch("project_forge.cron.self_improve_runner.fetch_ci_queue_issues",
                   return_value=fake_issues), \
             patch("project_forge.cron.self_improve_runner.gather_self_context",
                   return_value={}):
            mock_settings.anthropic_api_key = ""
            mock_settings.anthropic_model = "claude-sonnet-4-20250514"
            result = await run_self_improve_cycle()

        # Should skip, not crash
        assert result["results"][0]["status"] == "skipped"
