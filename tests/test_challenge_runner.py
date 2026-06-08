"""Tests for the autonomous challenge runner.

The challenge feature shipped in v0.5.0 as a dashboard-only POST endpoint.
Through May 2026 the DB collected exactly 5 records — one per human click.
This module gives the engine its own driver: each cycle it picks the
top-N highest-feasibility "new" ideas that have never been challenged,
runs a structured adversarial challenge, and persists the result.

Picking criteria mirror what a reviewer would do manually:
- Status must be "new" (not yet reviewed / archived / promoted).
- No existing row in the challenges table for that idea_id.
- Highest feasibility_score first — challenge the most promising ones,
  because killing weak ideas adds no signal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Challenge, Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "challenge_runner.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(
    name: str,
    *,
    score: float,
    status: str = "new",
    age_hours: int = 1,
) -> Idea:
    idea = Idea(
        name=name,
        tagline=f"tag {name}",
        description="A test idea description long enough to satisfy quality review minimums.",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="Real adopters with measurable pain.",
        feasibility_score=score,
        mvp_scope="Single-module change.",
        tech_stack=["python"],
        status=status,
    )
    idea.generated_at = datetime.now(UTC) - timedelta(hours=age_hours)
    return idea


class TestPickIdeasToChallenge:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_ideas(self, db):
        from project_forge.cron.challenge_runner import pick_ideas_to_challenge

        picks = await pick_ideas_to_challenge(db, limit=5)
        assert picks == []

    @pytest.mark.asyncio
    async def test_picks_top_score_new_only(self, db):
        from project_forge.cron.challenge_runner import pick_ideas_to_challenge

        await db.save_idea(_make_idea("low", score=0.5))
        await db.save_idea(_make_idea("mid", score=0.7))
        await db.save_idea(_make_idea("top", score=0.95))

        picks = await pick_ideas_to_challenge(db, limit=2)
        names = [i.name for i in picks]
        assert names == ["top", "mid"]

    @pytest.mark.asyncio
    async def test_skips_already_challenged(self, db):
        from project_forge.cron.challenge_runner import pick_ideas_to_challenge

        already = _make_idea("already", score=0.99)
        fresh = _make_idea("fresh", score=0.80)
        await db.save_idea(already)
        await db.save_idea(fresh)
        await db.save_challenge(
            Challenge(idea_id=already.id, question="?", response="."),
        )

        picks = await pick_ideas_to_challenge(db, limit=5)
        names = [i.name for i in picks]
        assert names == ["fresh"]

    @pytest.mark.asyncio
    async def test_skips_non_new_status(self, db):
        from project_forge.cron.challenge_runner import pick_ideas_to_challenge

        await db.save_idea(_make_idea("archived", score=0.99, status="archived"))
        await db.save_idea(_make_idea("approved", score=0.98, status="approved"))
        await db.save_idea(_make_idea("new1", score=0.70))

        picks = await pick_ideas_to_challenge(db, limit=10)
        assert [i.name for i in picks] == ["new1"]


class TestRunChallengeCycle:
    @pytest.mark.asyncio
    async def test_no_ideas_returns_zero(self, db):
        from project_forge.cron.challenge_runner import run_challenge_cycle

        result = await run_challenge_cycle(db)
        assert result == {"challenged": 0, "results": []}

    @pytest.mark.asyncio
    async def test_challenges_each_pick_and_persists(self, db):
        from project_forge.cron import challenge_runner

        await db.save_idea(_make_idea("a", score=0.9))
        await db.save_idea(_make_idea("b", score=0.85))

        async def _fake(idea, question, **_kw):
            return {
                "response": f"challenge response for {idea.name}",
                "verdict": "strengthen",
                "confidence": 0.8,
                "changes": [],
            }

        with patch.object(challenge_runner, "_challenge_idea", _fake):
            result = await challenge_runner.run_challenge_cycle(db, limit=5)

        assert result["challenged"] == 2
        # Persisted to the challenges table.
        row = await db.db.execute("SELECT COUNT(*) FROM challenges")
        assert (await row.fetchone())[0] == 2

    @pytest.mark.asyncio
    async def test_single_failure_does_not_block_remaining(self, db):
        from project_forge.cron import challenge_runner

        await db.save_idea(_make_idea("first", score=0.95))
        await db.save_idea(_make_idea("second", score=0.85))

        calls = []

        async def _fake(idea, question, **_kw):
            calls.append(idea.name)
            if idea.name == "first":
                raise RuntimeError("backend hiccup")
            return {
                "response": "ok",
                "verdict": "no_change",
                "confidence": 0.5,
                "changes": [],
            }

        with patch.object(challenge_runner, "_challenge_idea", _fake):
            result = await challenge_runner.run_challenge_cycle(db, limit=5)

        assert calls == ["first", "second"]
        # One challenge succeeded and got persisted.
        row = await db.db.execute("SELECT COUNT(*) FROM challenges")
        assert (await row.fetchone())[0] == 1
        statuses = sorted(r["status"] for r in result["results"])
        assert statuses == ["error", "ok"]

    @pytest.mark.asyncio
    async def test_uses_default_prompt_when_none_provided(self, db):
        """The runner provides a canned prompt for autonomous mode."""
        from project_forge.cron import challenge_runner

        await db.save_idea(_make_idea("solo", score=0.9))

        captured = {}

        async def _fake(idea, question, **kw):
            captured["question"] = question
            captured["kw"] = kw
            return {
                "response": "x",
                "verdict": "no_change",
                "confidence": 0.5,
                "changes": [],
            }

        with patch.object(challenge_runner, "_challenge_idea", _fake):
            await challenge_runner.run_challenge_cycle(db, limit=1)

        assert captured["question"]  # non-empty
        assert "tone" in captured["kw"]


class TestChallengeIdeaFnFallback:
    """The runner's _challenge_idea wrapper falls back gracefully when no
    LLM backend is reachable, so the loop doesn't pile up errors."""

    @pytest.mark.asyncio
    async def test_returns_no_change_when_backend_missing(self, db):
        from project_forge.cron import challenge_runner

        idea = _make_idea("x", score=0.8)

        with patch(
            "project_forge.cron.challenge_runner.resolve_backend",
            lambda: None,
        ):
            result = await challenge_runner._challenge_idea(
                idea, "is this real?", tone="skeptical",
            )

        assert result["verdict"] == "no_change"
        assert isinstance(result["response"], str)
        assert result["changes"] == []

    @pytest.mark.asyncio
    async def test_dispatches_to_backend_when_available(self, db):
        from project_forge.cron import challenge_runner

        idea = _make_idea("x", score=0.8)

        class _StubBackend:
            name = "stub"

            def call(self, _prompt):
                return (
                    '{"response":"good","verdict":"strengthen",'
                    '"confidence":0.9,"changes":[]}'
                )

        with patch(
            "project_forge.cron.challenge_runner.resolve_backend",
            lambda: _StubBackend(),
        ):
            result = await challenge_runner._challenge_idea(
                idea, "go", tone="skeptical",
            )

        assert result["verdict"] == "strengthen"
        assert result["confidence"] == 0.9
        assert result["response"] == "good"
