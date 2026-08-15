"""A generation cycle takes five to twelve minutes. It must not be silent.

The operator watched "probing venues, working one program…" for minutes
with no way to tell a working engine from a hung one — and the honest
answer was buried in a `ps` listing showing which `claude --print` call was
in flight. The engine knows exactly what it is doing at every step; it just
never said so.

This is deliberately an in-memory ring buffer, not a table. Progress is
ephemeral telemetry about a run in flight, it is worthless five minutes
after the run ends, and the durable record of what a cycle DID already
exists in `bot_probes`. A schema migration to hold log lines would be the
wrong trade.
"""

from __future__ import annotations

import pytest

from project_forge.engine import bot_progress


@pytest.fixture(autouse=True)
def _clean():
    bot_progress.reset()
    yield
    bot_progress.reset()


class TestRecording:
    def test_events_are_recorded_in_order(self):
        bot_progress.emit("probe", "sweeping 13 repositories")
        bot_progress.emit("pick", "Kalshi — reward tiers")

        events = bot_progress.recent()
        assert [e["stage"] for e in events] == ["probe", "pick"]
        assert events[1]["detail"] == "Kalshi — reward tiers"

    def test_every_event_is_timestamped(self):
        bot_progress.emit("probe", "x")
        assert bot_progress.recent()[0]["at"]

    def test_detail_is_optional(self):
        bot_progress.emit("scoring")
        assert bot_progress.recent()[0]["detail"] == ""

    def test_long_detail_is_truncated(self):
        bot_progress.emit("panel", "x" * 5000)
        assert len(bot_progress.recent()[0]["detail"]) <= 500

    def test_buffer_is_bounded(self):
        for i in range(bot_progress.MAX_EVENTS + 50):
            bot_progress.emit("tick", str(i))
        events = bot_progress.recent()
        assert len(events) == bot_progress.MAX_EVENTS
        # The newest survive; the oldest fall off.
        assert events[-1]["detail"] == str(bot_progress.MAX_EVENTS + 49)


class TestRunBoundaries:
    def test_starting_a_run_clears_the_previous_one(self):
        bot_progress.emit("probe", "old run")
        bot_progress.start_run()
        assert bot_progress.recent() == []

    def test_a_run_reports_as_active_until_it_finishes(self):
        bot_progress.start_run()
        assert bot_progress.status()["running"] is True
        bot_progress.finish_run("stored (vetted)")
        assert bot_progress.status()["running"] is False

    def test_the_outcome_survives_the_run(self):
        bot_progress.start_run()
        bot_progress.finish_run("panel flagged it")
        assert bot_progress.status()["outcome"] == "panel flagged it"

    def test_elapsed_seconds_are_reported(self):
        bot_progress.start_run()
        assert bot_progress.status()["elapsed_seconds"] >= 0

    def test_status_is_safe_before_any_run(self):
        assert bot_progress.status()["running"] is False
        assert bot_progress.recent() == []


@pytest.mark.asyncio
class TestEndpoint:
    async def test_progress_endpoint_returns_the_tail(self, tmp_path):
        from httpx import ASGITransport, AsyncClient

        from project_forge.web.app import app, db

        db.db_path = tmp_path / "progress.db"
        await db.connect()
        try:
            bot_progress.start_run()
            bot_progress.emit("probe", "sweeping venues")

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/money-bots/progress")
            assert resp.status_code == 200
            data = resp.json()
            assert data["running"] is True
            assert data["events"][0]["stage"] == "probe"
        finally:
            await db.close()


@pytest.mark.asyncio
class TestCadenceEmits:
    """The stages an operator actually wants to see."""

    async def test_a_quiet_cycle_still_narrates(self, db, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.setattr(ls, "_bot_fetch_programs", lambda: [])
        await ls._fire_bot_strategy(db)

        stages = [e["stage"] for e in bot_progress.recent()]
        assert "probe" in stages
        assert bot_progress.status()["running"] is False
        assert bot_progress.status()["outcome"]

    async def test_a_full_cycle_narrates_each_step(self, db, monkeypatch):
        from project_forge.engine.bot_depth import StressResult
        from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory
        from project_forge.web import lifespan_scheduler as ls

        idea = Idea(
            name="Narrated Strategy",
            tagline="t",
            description="d" * 60,
            category=IdeaCategory.INCENTIVE_CAPTURE,
            market_analysis="m" * 30,
            feasibility_score=0.7,
            mvp_scope="s",
            tech_stack=["python"],
        )
        idea.bot_spec = BotSpec(
            venue="Kalshi",
            venue_url="https://example.com",
            family=BotVenueFamily.PREDICTION_MARKETS,
            api_primitives=["REST"],
            mechanism="published reward budget",
            capital_floor_usd=500.0,
            capital_target_usd=5000.0,
            edge_decay="pool is fixed",
            kill_criteria=["reward below fees"],
            validation_plan=["14 days"],
        )

        class _Result:
            pass

        result = _Result()
        result.idea = idea

        async def _gen(*_a, **_k):
            return result

        async def _survive(i):
            return StressResult(idea=i, survived=True, passes=4)

        async def _score(_i):
            return 0.8

        monkeypatch.setattr(
            ls,
            "_bot_fetch_programs",
            lambda: [
                {
                    "venue": "Kalshi",
                    "family": "prediction-markets",
                    "category": "incentive-capture",
                    "title": "reward tiers",
                    "url": "https://example.com/1",
                    "summary": "s",
                    "program_score": 5,
                }
            ],
        )
        monkeypatch.setattr(ls, "_bot_generate", _gen)
        monkeypatch.setattr(ls, "_bot_stress", _survive)
        monkeypatch.setattr(ls, "_bot_score", _score)
        await ls._fire_bot_strategy(db)

        events = bot_progress.recent()
        stages = [e["stage"] for e in events]
        for expected in ("probe", "pick", "mechanism", "generate", "review", "gate", "store"):
            assert expected in stages, f"{expected} never narrated (got {stages})"

    async def test_the_slowest_step_is_announced_before_it_starts(self, db, monkeypatch):
        """The bug this caught: generation was only narrated AFTER it
        returned, so the tail sat frozen on "category" for the entire
        minutes-long model call — exactly the stretch the operator watches.

        A stage that takes time must announce itself on the way in."""
        await self.test_a_full_cycle_narrates_each_step(db, monkeypatch)

        events = bot_progress.recent()
        generate_events = [e for e in events if e["stage"] == "generate"]
        assert len(generate_events) >= 2, "generation must be announced before and after"
        assert "drafting" in generate_events[0]["detail"]

        # Nothing between picking the target and starting generation should
        # be a silent stretch: mechanism then generate, back to back.
        stages = [e["stage"] for e in events]
        assert stages.index("mechanism") < stages.index("generate")
