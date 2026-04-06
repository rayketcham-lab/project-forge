"""Tests for the automated idea review cycle.

Problem: ~1900 ideas and 56 super ideas sit forever with their original scores.
No automated second pass, no round-robin review, no staleness detection.
Ideas either flip (strengthen/pivot) or fold (archive/kill) — but nobody's checking.

Fix: A review runner that:
1. Picks the oldest-unreviewed ideas in round-robin batches
2. Sends each to Claude for re-evaluation (verdict: keep/pivot/archive/kill)
3. Tracks last_reviewed_at and review_verdict per idea
4. Auto-archives ideas that score "kill" with high confidence
5. Runs as a scheduled cron job
"""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test_review.db")
    await d.connect()
    yield d
    await d.close()


def _idea(name: str, category=IdeaCategory.SECURITY_TOOL, score=0.75, status="new", generated_at=None, **kw) -> Idea:
    defaults = dict(
        name=name,
        tagline=f"Tagline for {name}",
        description="Test description.",
        category=category,
        market_analysis="Market need.",
        feasibility_score=score,
        mvp_scope="Build it.",
        tech_stack=["python"],
        status=status,
    )
    if generated_at:
        defaults["generated_at"] = generated_at
    defaults.update(kw)
    return Idea(**defaults)


# ---------------------------------------------------------------------------
# 1. DB: fetch ideas needing review (oldest unreviewed first)
# ---------------------------------------------------------------------------


class TestFetchIdeasForReview:
    """DB should return ideas that haven't been reviewed, oldest first."""

    @pytest.mark.asyncio
    async def test_returns_ideas_never_reviewed(self, db):
        """Ideas with no last_reviewed_at should be returned."""
        old = _idea("Old Idea", generated_at=datetime(2026, 1, 1, tzinfo=UTC))
        new = _idea("New Idea", generated_at=datetime(2026, 4, 1, tzinfo=UTC))
        await db.save_idea(old)
        await db.save_idea(new)

        batch = await db.fetch_ideas_for_review(limit=10)
        assert len(batch) == 2
        # Oldest first
        assert batch[0].name == "Old Idea"

    @pytest.mark.asyncio
    async def test_skips_already_reviewed_recently(self, db):
        """Ideas reviewed within the review window should be skipped."""
        idea = _idea("Recently Reviewed")
        await db.save_idea(idea)
        # Mark as reviewed
        await db.record_review(idea.id, "keep", 0.8)

        batch = await db.fetch_ideas_for_review(limit=10, min_age_days=7)
        assert len(batch) == 0

    @pytest.mark.asyncio
    async def test_returns_stale_reviews(self, db):
        """Ideas reviewed long ago should come back for re-review."""
        idea = _idea("Stale Reviewed", generated_at=datetime(2026, 1, 1, tzinfo=UTC))
        await db.save_idea(idea)
        # Mark as reviewed 30 days ago
        await db.record_review(idea.id, "keep", 0.8, reviewed_at=datetime(2026, 3, 1, tzinfo=UTC))

        batch = await db.fetch_ideas_for_review(limit=10, min_age_days=7)
        assert len(batch) == 1

    @pytest.mark.asyncio
    async def test_respects_batch_limit(self, db):
        for i in range(20):
            await db.save_idea(_idea(f"Idea {i}"))

        batch = await db.fetch_ideas_for_review(limit=5)
        assert len(batch) == 5

    @pytest.mark.asyncio
    async def test_skips_rejected_and_archived(self, db):
        """Rejected and archived ideas should not be fetched for review."""
        await db.save_idea(_idea("Active", status="new"))
        await db.save_idea(_idea("Rejected", status="rejected"))
        await db.save_idea(_idea("Archived", status="archived"))

        batch = await db.fetch_ideas_for_review(limit=10)
        assert len(batch) == 1
        assert batch[0].name == "Active"


# ---------------------------------------------------------------------------
# 2. DB: record review results
# ---------------------------------------------------------------------------


class TestRecordReview:
    """DB should store review verdicts and timestamps."""

    @pytest.mark.asyncio
    async def test_record_review_stores_verdict(self, db):
        idea = _idea("Reviewed Idea")
        await db.save_idea(idea)

        await db.record_review(idea.id, "keep", 0.85)

        reviews = await db.get_idea_reviews(idea.id)
        assert len(reviews) == 1
        assert reviews[0]["verdict"] == "keep"
        assert reviews[0]["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_multiple_reviews_tracked(self, db):
        """Multiple reviews on the same idea should all be stored."""
        idea = _idea("Multi-Review")
        await db.save_idea(idea)

        await db.record_review(idea.id, "keep", 0.7)
        await db.record_review(idea.id, "pivot", 0.6)

        reviews = await db.get_idea_reviews(idea.id)
        assert len(reviews) == 2


# ---------------------------------------------------------------------------
# 3. Review runner: orchestration
# ---------------------------------------------------------------------------


class TestReviewRunner:
    """The review runner picks ideas, sends to Claude, records verdicts."""

    @pytest.mark.asyncio
    async def test_review_cycle_processes_batch(self, db):
        from project_forge.cron.review_runner import run_review_cycle

        await db.save_idea(_idea("Idea A"))
        await db.save_idea(_idea("Idea B"))

        fake_review = {"verdict": "keep", "confidence": 0.8, "reasoning": "Still viable.", "suggestions": []}

        with (
            patch("project_forge.cron.review_runner._review_idea_with_api", return_value=fake_review),
            patch("project_forge.cron.review_runner._get_api_key", return_value="fake-key"),
        ):
            result = await run_review_cycle(db, batch_size=5)

        assert result["reviewed"] == 2
        assert result["results"][0]["verdict"] == "keep"

    @pytest.mark.asyncio
    async def test_review_cycle_auto_archives_kills(self, db):
        """Ideas with 'kill' verdict and high confidence get auto-archived."""
        from project_forge.cron.review_runner import run_review_cycle

        idea = _idea("Doomed Idea")
        await db.save_idea(idea)

        fake_review = {"verdict": "kill", "confidence": 0.9, "reasoning": "Completely superseded.", "suggestions": []}

        with (
            patch("project_forge.cron.review_runner._review_idea_with_api", return_value=fake_review),
            patch("project_forge.cron.review_runner._get_api_key", return_value="fake-key"),
        ):
            await run_review_cycle(db, batch_size=5)

        updated = await db.get_idea(idea.id)
        assert updated.status == "archived"

    @pytest.mark.asyncio
    async def test_review_cycle_does_not_archive_low_confidence_kills(self, db):
        """Kill verdict with low confidence should NOT auto-archive."""
        from project_forge.cron.review_runner import run_review_cycle

        idea = _idea("Maybe Doomed")
        await db.save_idea(idea)

        fake_review = {"verdict": "kill", "confidence": 0.4, "reasoning": "Uncertain.", "suggestions": []}

        with (
            patch("project_forge.cron.review_runner._review_idea_with_api", return_value=fake_review),
            patch("project_forge.cron.review_runner._get_api_key", return_value="fake-key"),
        ):
            await run_review_cycle(db, batch_size=5)

        updated = await db.get_idea(idea.id)
        assert updated.status == "new"  # Not archived

    @pytest.mark.asyncio
    async def test_review_cycle_skips_when_no_ideas(self, db):
        from project_forge.cron.review_runner import run_review_cycle

        with patch("project_forge.cron.review_runner._review_idea") as mock:
            result = await run_review_cycle(db, batch_size=5)

        mock.assert_not_called()
        assert result["reviewed"] == 0

    @pytest.mark.asyncio
    async def test_review_cycle_handles_claude_error(self, db):
        """If Claude fails for one idea, others should still be processed."""
        from project_forge.cron.review_runner import run_review_cycle

        await db.save_idea(_idea("Good Idea"))
        await db.save_idea(_idea("Bad Idea"))

        call_count = 0

        async def flaky_review(idea, api_key="", model=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("API error")
            return {"verdict": "keep", "confidence": 0.7, "reasoning": "Fine.", "suggestions": []}

        with (
            patch("project_forge.cron.review_runner._review_idea_with_api", side_effect=flaky_review),
            patch("project_forge.cron.review_runner._get_api_key", return_value="fake-key"),
        ):
            result = await run_review_cycle(db, batch_size=5)

        assert result["reviewed"] == 2
        errors = [r for r in result["results"] if r["status"] == "error"]
        successes = [r for r in result["results"] if r["status"] == "reviewed"]
        assert len(errors) == 1
        assert len(successes) == 1


# ---------------------------------------------------------------------------
# 4. Review prompt builds correctly
# ---------------------------------------------------------------------------


class TestBuildReviewPrompt:
    """The review prompt includes idea details and asks for structured verdict."""

    def test_prompt_includes_idea_name_and_description(self):
        from project_forge.cron.review_runner import build_review_prompt

        idea = _idea("PKI Scanner", description="Scans certificates for expiry.")
        prompt = build_review_prompt(idea)

        assert "PKI Scanner" in prompt
        assert "Scans certificates" in prompt

    def test_prompt_requests_json_verdict(self):
        from project_forge.cron.review_runner import build_review_prompt

        idea = _idea("Test Idea")
        prompt = build_review_prompt(idea)

        assert "verdict" in prompt
        assert "keep" in prompt
        assert "kill" in prompt
        assert "JSON" in prompt or "json" in prompt

    def test_prompt_includes_age(self):
        from project_forge.cron.review_runner import build_review_prompt

        old_idea = _idea("Old Idea", generated_at=datetime(2026, 1, 1, tzinfo=UTC))
        prompt = build_review_prompt(old_idea)

        assert "Generated" in prompt or "Age" in prompt or "days" in prompt.lower()
