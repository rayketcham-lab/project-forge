"""Tests for the multi-cadence FastAPI lifespan scheduler.

Companion to `test_lifespan_scheduler.py` (which covers the introspect
cadence). The original scheduler only ran introspect; this test file
covers the generic `Cadence` machinery and the four additional cadences
that pulled idea generation, review, self-improve, and challenges back
in-process after the corresponding systemd timers became unreachable
from the bwrap-sandboxed runtime.

The four added cadences and their watermarks:
- expand:       1h interval, watermark = MAX(ideas.generated_at)
                regardless of category. Restores horizontal cross-cat +
                super-idea production.
- review:       12h interval, watermark = MAX(idea_reviews.reviewed_at).
                Auto-archive sweeps so the active set stays clean.
- self_improve: 6h interval, pure clock-based (no DB watermark — the
                runner queries GitHub for ci-queue issues).
- challenge:    168h interval, watermark = MAX(challenges.created_at).
                Picks top-N highest-feasibility unchallenged "new"
                ideas and runs one structured challenge per idea.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "multi_sched.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(
    name: str,
    *,
    category: IdeaCategory = IdeaCategory.OBSERVABILITY,
    score: float = 0.85,
) -> Idea:
    return Idea(
        name=name,
        tagline=f"tagline for {name}",
        description=(
            "A test idea description long enough to satisfy the quality "
            "review minimum body length so we exercise the right code path."
        ),
        category=category,
        market_analysis="Plausible market with concrete adopters.",
        feasibility_score=score,
        mvp_scope="Minimum-viable change scoped to a single module.",
        tech_stack=["python"],
    )


# --------------------------------------------------------------------------- #
# Generic Cadence machinery
# --------------------------------------------------------------------------- #


class TestCadenceTick:
    """Generic cadence_tick: fires runner only when delay_query returns <= 0."""

    @pytest.mark.asyncio
    async def test_fires_when_delay_query_returns_zero(self, db):
        from project_forge.web.lifespan_scheduler import Cadence, cadence_tick

        runner = AsyncMock()

        async def _delay(_db, _interval):
            return 0.0

        cadence = Cadence(
            name="t",
            interval=timedelta(hours=1),
            runner=runner,
            delay_query=_delay,
        )
        await cadence_tick(db, cadence)
        runner.assert_awaited_once_with(db)

    @pytest.mark.asyncio
    async def test_skips_when_delay_query_returns_positive(self, db):
        from project_forge.web.lifespan_scheduler import Cadence, cadence_tick

        runner = AsyncMock()

        async def _delay(_db, _interval):
            return 3600.0

        cadence = Cadence(
            name="t",
            interval=timedelta(hours=1),
            runner=runner,
            delay_query=_delay,
        )
        await cadence_tick(db, cadence)
        runner.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_unconditionally_when_no_delay_query(self, db):
        """Pure clock-based cadences (no DB watermark) fire on every tick."""
        from project_forge.web.lifespan_scheduler import Cadence, cadence_tick

        runner = AsyncMock()
        cadence = Cadence(
            name="t",
            interval=timedelta(hours=6),
            runner=runner,
            delay_query=None,
        )
        await cadence_tick(db, cadence)
        runner.assert_awaited_once_with(db)

    @pytest.mark.asyncio
    async def test_swallows_runner_exception(self, db):
        from project_forge.web.lifespan_scheduler import Cadence, cadence_tick

        async def _boom(_db):
            raise RuntimeError("runner failed")

        cadence = Cadence(
            name="t",
            interval=timedelta(hours=1),
            runner=_boom,
            delay_query=None,
        )
        # Must not raise — the loop has to survive.
        await cadence_tick(db, cadence)


class TestSupervisor:
    """start_scheduler returns one supervisor task that owns N child loops."""

    @pytest.mark.asyncio
    async def test_supervisor_starts_all_cadences(self, db):
        from project_forge.web import lifespan_scheduler
        from project_forge.web.lifespan_scheduler import Cadence

        a_fired = asyncio.Event()
        b_fired = asyncio.Event()

        async def _a(_db):
            a_fired.set()

        async def _b(_db):
            b_fired.set()

        cadences = [
            Cadence(name="a", interval=timedelta(hours=1), runner=_a, tick_interval=0.01),
            Cadence(name="b", interval=timedelta(hours=1), runner=_b, tick_interval=0.01),
        ]
        task = lifespan_scheduler.start_scheduler(db, cadences=cadences)
        try:
            await asyncio.wait_for(
                asyncio.gather(a_fired.wait(), b_fired.wait()),
                timeout=2.0,
            )
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_supervisor_cancel_cancels_all_children(self, db):
        """Cancelling the supervisor must propagate to every child loop."""
        from project_forge.web import lifespan_scheduler
        from project_forge.web.lifespan_scheduler import Cadence

        cadences = [
            Cadence(name=str(i), interval=timedelta(hours=1), runner=AsyncMock(), tick_interval=0.5) for i in range(3)
        ]
        task = lifespan_scheduler.start_scheduler(db, cadences=cadences)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.done()
        # No leftover scheduler tasks should remain in the loop.
        leaked = [t for t in asyncio.all_tasks() if t.get_name().startswith("sched-")]
        assert leaked == []

    @pytest.mark.asyncio
    async def test_supervisor_isolates_failure(self, db):
        """One cadence raising must not stop sibling cadences."""
        from project_forge.web import lifespan_scheduler
        from project_forge.web.lifespan_scheduler import Cadence

        good_fired = asyncio.Event()

        async def _bad(_db):
            raise RuntimeError("bad")

        async def _good(_db):
            good_fired.set()

        cadences = [
            Cadence(name="bad", interval=timedelta(hours=1), runner=_bad, tick_interval=0.01),
            Cadence(name="good", interval=timedelta(hours=1), runner=_good, tick_interval=0.01),
        ]
        task = lifespan_scheduler.start_scheduler(db, cadences=cadences)
        try:
            await asyncio.wait_for(good_fired.wait(), timeout=2.0)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# --------------------------------------------------------------------------- #
# Expand cadence (1h, all-category watermark)
# --------------------------------------------------------------------------- #


class TestExpandCadence:
    @pytest.mark.asyncio
    async def test_delay_zero_when_db_empty(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_delay_positive_when_recent_idea_any_category(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Recent", category=IdeaCategory.OBSERVABILITY)
        idea.generated_at = datetime.now(UTC) - timedelta(minutes=10)
        await db.save_idea(idea)

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert 40 * 60 < delay < 60 * 60

    @pytest.mark.asyncio
    async def test_delay_zero_when_last_idea_stale(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_expand

        idea = _make_idea("Stale", category=IdeaCategory.PRIVACY)
        idea.generated_at = datetime.now(UTC) - timedelta(hours=4)
        await db.save_idea(idea)

        delay = await seconds_until_next_expand(db, interval=timedelta(hours=1))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_fire_expand_invokes_horizontal_cycle(self, db):
        from project_forge.web import lifespan_scheduler

        called = AsyncMock(return_value=[])
        with patch(
            "project_forge.cron.horizontal.run_horizontal_cycle",
            called,
        ):
            await lifespan_scheduler._fire_expand(db)
        called.assert_awaited_once_with(db)


# --------------------------------------------------------------------------- #
# Review cadence (12h, idea_reviews watermark)
# --------------------------------------------------------------------------- #


class TestReviewCadence:
    @pytest.mark.asyncio
    async def test_delay_zero_when_no_reviews(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_review

        delay = await seconds_until_next_review(db, interval=timedelta(hours=12))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_delay_positive_when_recent_review(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_review

        idea = _make_idea("Reviewed")
        await db.save_idea(idea)
        await db.record_review(
            idea_id=idea.id,
            verdict="keep",
            confidence=0.8,
            reasoning="ok",
            suggestions=[],
        )

        delay = await seconds_until_next_review(db, interval=timedelta(hours=12))
        # Recorded just now; ~12h should remain.
        assert 11 * 3600 < delay <= 12 * 3600

    @pytest.mark.asyncio
    async def test_fire_review_invokes_review_cycle(self, db):
        from project_forge.web import lifespan_scheduler

        called = AsyncMock(return_value={"reviewed": 0, "results": []})
        with patch(
            "project_forge.cron.review_runner.run_review_cycle",
            called,
        ):
            await lifespan_scheduler._fire_review(db)
        called.assert_awaited_once()
        # First arg is the db handle.
        assert called.await_args.args[0] is db


# --------------------------------------------------------------------------- #
# Self-improve cadence (6h, pure clock — no DB watermark)
# --------------------------------------------------------------------------- #


class TestSelfImproveCadence:
    @pytest.mark.asyncio
    async def test_fire_self_improve_invokes_runner(self, db):
        from project_forge.web import lifespan_scheduler

        called = AsyncMock(return_value={"processed": 0, "results": []})
        with patch(
            "project_forge.cron.self_improve_runner.run_self_improve_cycle",
            called,
        ):
            await lifespan_scheduler._fire_self_improve(db)
        called.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_fire_self_improve_swallows_exception(self, db):
        from project_forge.web import lifespan_scheduler

        async def _boom():
            raise RuntimeError("github down")

        with patch(
            "project_forge.cron.self_improve_runner.run_self_improve_cycle",
            _boom,
        ):
            await lifespan_scheduler._fire_self_improve(db)


# --------------------------------------------------------------------------- #
# Challenge cadence (168h, challenges.created_at watermark)
# --------------------------------------------------------------------------- #


class TestChallengeCadence:
    @pytest.mark.asyncio
    async def test_delay_zero_when_no_challenges(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_challenge

        delay = await seconds_until_next_challenge(db, interval=timedelta(hours=168))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_delay_positive_when_recent_challenge(self, db):
        from project_forge.models import Challenge
        from project_forge.web.lifespan_scheduler import seconds_until_next_challenge

        idea = _make_idea("Target")
        await db.save_idea(idea)
        challenge = Challenge(
            idea_id=idea.id,
            question="why",
            response="because",
        )
        await db.save_challenge(challenge)

        delay = await seconds_until_next_challenge(db, interval=timedelta(hours=168))
        assert 167 * 3600 < delay <= 168 * 3600

    @pytest.mark.asyncio
    async def test_fire_challenge_invokes_challenge_runner(self, db):
        from project_forge.web import lifespan_scheduler

        called = AsyncMock(return_value={"challenged": 0, "results": []})
        with patch(
            "project_forge.cron.challenge_runner.run_challenge_cycle",
            called,
        ):
            await lifespan_scheduler._fire_challenge(db)
        called.assert_awaited_once()
        assert called.await_args.args[0] is db
