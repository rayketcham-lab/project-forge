"""Tests for the in-process introspect scheduler.

Background — the daily introspect runner used to live in
`/etc/systemd/system/project-forge-introspect.timer`. That timer is unreachable
from the bwrap-sandboxed agent (no DBUS bus, no sudo, no write access to
`/etc/systemd/`). When the timer stopped firing on 2026-05-09 the Think Tank
"Forge Lab" pane went stale for six days because nothing was triggering new
proposals.

The scheduler defined in `project_forge.web.lifespan_scheduler` moves that
cadence into the FastAPI lifespan so file writes auto-deploy and the engine
manages its own SI cadence without touching systemd.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "scheduler.db")
    await database.connect()
    yield database
    await database.close()


def _make_idea(name: str) -> Idea:
    return Idea(
        name=name,
        tagline="Test SI proposal",
        description=(
            "A self-improvement proposal long enough to satisfy the quality "
            "review minimum body length so dedup is the only gate exercised."
        ),
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Improves engine reliability and code clarity.",
        feasibility_score=0.85,
        mvp_scope="Minimum-viable change scoped to a single module.",
        tech_stack=["python"],
    )


class TestIntrospectWatermark:
    """The watermark drives when the in-process loop next fires."""

    @pytest.mark.asyncio
    async def test_returns_zero_when_db_empty(self, db):
        """An empty database means we have never fired — fire immediately."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        result = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_returns_positive_when_recent_accept(self, db):
        """A recent accepted SI idea pushes the next fire into the future."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        idea = _make_idea("Recent SI")
        idea.generated_at = datetime.now(UTC) - timedelta(hours=1)
        await db.save_idea(idea)

        result = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        # Roughly 23 hours remain. Allow a wide band for test execution time.
        assert 22 * 3600 < result < 24 * 3600

    @pytest.mark.asyncio
    async def test_returns_zero_when_last_accept_is_stale(self, db):
        """A stale accepted SI idea means we are overdue — fire immediately."""
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        idea = _make_idea("Stale SI")
        idea.generated_at = datetime.now(UTC) - timedelta(hours=48)
        await db.save_idea(idea)

        result = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_filtered_attempt_also_counts_as_a_fire(self, db):
        """A filtered SI attempt proves the runner fired even if no idea landed.

        Without this, a streak of 100%-dedup days would let the loop hammer
        the LLM backend every iteration of the scheduler.
        """
        from project_forge.web.lifespan_scheduler import seconds_until_next_introspect

        # Insert directly into filtered_ideas to mirror what dedup.filter_and_save does
        await db.db.execute(
            "INSERT INTO filtered_ideas "
            "(id, idea_name, idea_tagline, idea_category, "
            " filter_reason, filtered_at, original_idea_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "test-id",
                "Recent filtered",
                "tagline",
                "self-improvement",
                "duplicate:test",
                (datetime.now(UTC) - timedelta(minutes=30)).isoformat(),
                "{}",
            ),
        )
        await db.db.commit()

        result = await seconds_until_next_introspect(db, interval=timedelta(hours=24))
        # ~23.5 hours remain
        assert 23 * 3600 < result < 24 * 3600


class TestIntrospectTick:
    """One iteration of the loop."""

    @pytest.mark.asyncio
    async def test_tick_fires_runner_when_overdue(self, db):
        """When the watermark is stale, the runner is invoked once."""
        from project_forge.web import lifespan_scheduler

        called = AsyncMock()
        with patch.object(lifespan_scheduler, "_fire_introspect", called):
            await lifespan_scheduler.introspect_tick(db, interval=timedelta(hours=24))

        called.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tick_skips_runner_when_fresh(self, db):
        """When the watermark is fresh, the runner is not invoked."""
        from project_forge.web import lifespan_scheduler

        idea = _make_idea("Fresh SI")
        idea.generated_at = datetime.now(UTC) - timedelta(minutes=10)
        await db.save_idea(idea)

        called = AsyncMock()
        with patch.object(lifespan_scheduler, "_fire_introspect", called):
            await lifespan_scheduler.introspect_tick(db, interval=timedelta(hours=24))

        called.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tick_swallows_runner_exceptions(self, db):
        """A failing introspect run must not propagate — the loop survives."""
        from project_forge.web import lifespan_scheduler

        async def _boom(_db):
            raise RuntimeError("backend exploded")

        with patch.object(lifespan_scheduler, "_fire_introspect", _boom):
            # Should not raise
            await lifespan_scheduler.introspect_tick(db, interval=timedelta(hours=24))


class TestEnvParsingSafety:
    """Fix #77 — INITIAL_DELAY parsing must warn-and-fallback on a bad value
    instead of crashing uvicorn at import time."""

    def test_bad_initial_delay_value_falls_back(self, monkeypatch):
        from project_forge.web.lifespan_scheduler import _seconds_from_env

        monkeypatch.setenv("FORGE_TEST_BAD", "fast")
        result = _seconds_from_env("FORGE_TEST_BAD", 60.0)
        assert result == timedelta(seconds=60)

    def test_empty_initial_delay_value_falls_back(self, monkeypatch):
        from project_forge.web.lifespan_scheduler import _seconds_from_env

        monkeypatch.setenv("FORGE_TEST_BAD", "")
        result = _seconds_from_env("FORGE_TEST_BAD", 60.0)
        assert result == timedelta(seconds=60)

    def test_valid_numeric_initial_delay_honoured(self, monkeypatch):
        from project_forge.web.lifespan_scheduler import _seconds_from_env

        monkeypatch.setenv("FORGE_TEST_GOOD", "15.5")
        result = _seconds_from_env("FORGE_TEST_GOOD", 60.0)
        assert result == timedelta(seconds=15.5)

    def test_unset_env_uses_default(self, monkeypatch):
        from project_forge.web.lifespan_scheduler import _seconds_from_env

        monkeypatch.delenv("FORGE_TEST_UNSET", raising=False)
        result = _seconds_from_env("FORGE_TEST_UNSET", 42.0)
        assert result == timedelta(seconds=42)


class TestSchedulerLifecycle:
    """The scheduler task starts and cancels cleanly."""

    @pytest.mark.asyncio
    async def test_start_and_stop_does_not_leak(self, db):
        """The background task must be cancellable on shutdown."""
        import asyncio

        from project_forge.web import lifespan_scheduler

        task = lifespan_scheduler.start_scheduler(db, tick_interval=0.05)
        try:
            assert isinstance(task, asyncio.Task)
            await asyncio.sleep(0.1)
            assert not task.done()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert task.done()
