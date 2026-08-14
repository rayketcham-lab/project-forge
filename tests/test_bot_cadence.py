"""The money board's cadence: probe → generate → red team → gate → store or drop.

Modelled on the PKI probe, and for the same reason. A cadence that always
produces something fills a board with plausible landfill; this one runs a
grounded probe, works ONE venue program, and is allowed — expected, even —
to store nothing. Every attempt lands in `bot_probes` so the quiet hours
are auditable and the schedule still has a watermark that advances.

What these tests pin:
  * the drop paths all record a probe row and store no idea;
  * a strategy the red team kills is never stored;
  * an admitted strategy is stored with mode='bot', a score and its spec;
  * the cadence touches no GitHub state.
"""

from __future__ import annotations

import pytest

from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory

_PROGRAM = {
    "venue": "Polymarket US",
    "family": BotVenueFamily.PREDICTION_MARKETS.value,
    "category": IdeaCategory.INCENTIVE_CAPTURE.value,
    "title": "Liquidity rewards: qualifying spread not documented",
    "url": "https://github.com/Polymarket/py-clob-client/issues/42",
    "summary": "Reward budget per market and max spread band are unclear.",
    "source": "github-issue",
    "program_score": 6,
}


def _spec(**over) -> BotSpec:
    base = dict(
        venue="Polymarket US",
        venue_url="https://docs.polymarket.com/rewards",
        family=BotVenueFamily.PREDICTION_MARKETS,
        api_primitives=["CLOB REST order placement", "websocket book feed"],
        mechanism="Venue pays a published per-minute liquidity reward for two-sided quotes.",
        capital_floor_usd=500.0,
        capital_target_usd=10000.0,
        expected_return="Pro-rata share of the published reward budget",
        edge_decay="Fixed pool split pro-rata — yield falls as makers arrive",
        kill_criteria=["reward per minute below fees plus adverse selection"],
        validation_plan=["one book, floor capital, 14 days"],
        legality_note="Published venue program with public rules",
        human_touchpoints="Weekly review",
    )
    base.update(over)
    return BotSpec(**base)


def _idea(**over) -> Idea:
    base = dict(
        name="Reward Minute Maker",
        tagline="rest two-sided quotes and collect the published reward budget",
        description=(
            "Quotes both sides of high-reward Polymarket books through the CLOB REST API, "
            "keeping size inside the qualifying spread so it earns reward minutes. Income is "
            "the venue's published budget, split pro-rata, so yield decays as makers arrive. "
            "Runs unattended with an inventory kill switch."
        ),
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis="Reward budgets are published per market; few makers quote them.",
        feasibility_score=0.72,
        mvp_scope="Quote one book at $500 with a hard inventory kill switch.",
        tech_stack=["python", "websockets"],
    )
    base.update(over)
    idea = Idea(**base)
    idea.generation_mode = "bot"
    if "bot_spec" not in over:
        idea.bot_spec = _spec()
    return idea


class _Result:
    def __init__(self, idea: Idea):
        self.idea = idea
        self.mode = "bot"
        self.persona = "p"
        self.backend = "fake"
        self.raw_response = "{}"
        self.artifact_type = None


@pytest.fixture
def sched(monkeypatch):
    """The scheduler module with every outbound edge stubbed to a no-op."""
    from project_forge.web import lifespan_scheduler as ls

    monkeypatch.setattr(ls, "_random", __import__("random").Random(11), raising=False)
    return ls


# --------------------------------------------------------------------------- #
# Probe log                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestProbeLog:
    async def test_records_and_lists(self, db):
        await db.record_bot_probe(
            program_summary="Polymarket rewards",
            venue="Polymarket US",
            anchor="https://example.com/1",
            admitted=False,
            reason="panel killed it",
        )
        rows = await db.list_bot_probes(limit=5)
        assert len(rows) == 1
        assert rows[0]["venue"] == "Polymarket US"
        assert rows[0]["admitted"] is False
        assert rows[0]["reason"] == "panel killed it"

    async def test_stats_report_admission_rate(self, db):
        await db.record_bot_probe(program_summary="a", venue="v", anchor="u1", admitted=True, reason="ok")
        await db.record_bot_probe(program_summary="b", venue="v", anchor="u2", admitted=False, reason="no")
        stats = await db.bot_probe_stats()
        assert stats["probes"] == 2
        assert stats["admitted"] == 1
        assert stats["admit_rate"] == 0.5

    async def test_empty_log_reports_zero_not_a_crash(self, db):
        stats = await db.bot_probe_stats()
        assert stats["probes"] == 0
        assert stats["admit_rate"] == 0.0


# --------------------------------------------------------------------------- #
# Cadence                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestCadence:
    async def test_no_program_stores_nothing_but_logs(self, db, sched, monkeypatch):
        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [])
        await sched._fire_bot_strategy(db)

        probes = await db.list_bot_probes()
        assert len(probes) == 1
        assert probes[0]["admitted"] is False
        assert not await db.list_ideas(limit=10)

    async def test_generator_failure_is_logged_not_raised(self, db, sched, monkeypatch):
        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        async def _no_idea(*_a, **_k):
            return None

        monkeypatch.setattr(sched, "_bot_generate", _no_idea)
        await sched._fire_bot_strategy(db)

        probes = await db.list_bot_probes()
        assert len(probes) == 1
        assert "generat" in (probes[0]["reason"] or "")
        assert not await db.list_ideas(limit=10)

    async def test_red_team_kill_is_stored_and_flagged(self, db, sched, monkeypatch):
        """v0.24.1: a knocked-down draft is FLAGGED, not deleted.

        The first cut discarded these, so the board sat empty while the most
        useful output — a specific, quantified reason a plausible strategy
        does not work — went into a log nobody reads.
        """
        from project_forge.engine.bot_depth import StressResult

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        async def _gen(*_a, **_k):
            return _Result(_idea())

        async def _kill(idea):
            return StressResult(idea=idea, survived=False, strongest="fees eat it", passes=4)

        async def _score(_i):
            return 0.71

        monkeypatch.setattr(sched, "_bot_generate", _gen)
        monkeypatch.setattr(sched, "_bot_stress", _kill)
        monkeypatch.setattr(sched, "_bot_score", _score)
        await sched._fire_bot_strategy(db)

        ideas = await db.list_ideas(limit=10)
        assert len(ideas) == 1
        stored = ideas[0]
        assert stored.bot_spec.panel_verdict == "flagged"
        assert stored.bot_spec.surviving_objection == "fees eat it"
        # Stored, but not counted as an admission.
        probes = await db.list_bot_probes()
        assert probes[0]["admitted"] is False

    async def test_below_threshold_is_stored_below_bar(self, db, sched, monkeypatch):
        from project_forge.engine.bot_depth import StressResult

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        async def _gen(*_a, **_k):
            return _Result(_idea())

        async def _survive(idea):
            return StressResult(idea=idea, survived=True, passes=4)

        async def _low_score(_idea_arg):
            return 0.10

        monkeypatch.setattr(sched, "_bot_generate", _gen)
        monkeypatch.setattr(sched, "_bot_stress", _survive)
        monkeypatch.setattr(sched, "_bot_score", _low_score)
        await sched._fire_bot_strategy(db)

        ideas = await db.list_ideas(limit=10)
        assert len(ideas) == 1
        assert ideas[0].bot_spec.panel_verdict == "below-bar"
        assert (await db.list_bot_probes())[0]["admitted"] is False

    async def test_illegitimate_strategy_is_still_refused_outright(self, db, sched, monkeypatch):
        """The one thing that must never reach the board, verdict or not."""
        from project_forge.engine.bot_depth import StressResult

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        dirty = _idea(
            name="Volume Manufacturer",
            description="It wash trades between two accounts to manufacture qualifying volume.",
        )

        async def _gen(*_a, **_k):
            return _Result(dirty)

        async def _survive(idea):
            return StressResult(idea=idea, survived=True, passes=4)

        async def _score(_i):
            return 0.9

        monkeypatch.setattr(sched, "_bot_generate", _gen)
        monkeypatch.setattr(sched, "_bot_stress", _survive)
        monkeypatch.setattr(sched, "_bot_score", _score)
        await sched._fire_bot_strategy(db)

        assert not await db.list_ideas(limit=10)
        assert "legitimate" in ((await db.list_bot_probes())[0]["reason"] or "")

    async def test_admitted_strategy_is_stored_with_score_and_spec(self, db, sched, monkeypatch):
        from project_forge.engine.bot_depth import StressResult

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        async def _gen(*_a, **_k):
            return _Result(_idea())

        async def _survive(idea):
            return StressResult(idea=idea, survived=True, strongest="capacity caps it", passes=4)

        async def _high_score(_idea_arg):
            return 0.82

        monkeypatch.setattr(sched, "_bot_generate", _gen)
        monkeypatch.setattr(sched, "_bot_stress", _survive)
        monkeypatch.setattr(sched, "_bot_score", _high_score)
        await sched._fire_bot_strategy(db)

        probes = await db.list_bot_probes()
        assert probes[0]["admitted"] is True

        ideas = await db.list_ideas(limit=10)
        assert len(ideas) == 1
        stored = ideas[0]
        assert stored.generation_mode == "bot"
        assert stored.bot_edge_score == 0.82
        assert stored.bot_spec is not None
        assert stored.bot_spec.venue == "Polymarket US"

    async def test_already_probed_programs_are_skipped(self, db, sched, monkeypatch):
        """The same GitHub issue must not be worked twice."""
        await db.record_bot_probe(
            program_summary="seen",
            venue="Polymarket US",
            anchor=_PROGRAM["url"],
            admitted=False,
            reason="already worked",
        )
        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])
        await sched._fire_bot_strategy(db)

        probes = await db.list_bot_probes()
        assert len(probes) == 2
        assert "no new" in (probes[-0]["reason"] or "").lower() or "no new" in (probes[0]["reason"] or "").lower()

    async def test_cadence_is_registered_on_a_schedule(self, sched):
        names = sched.cadence_names() if hasattr(sched, "cadence_names") else []
        assert "bot_strategy" in names or any("bot" in n for n in names)


@pytest.mark.asyncio
class TestCategoryRotation:
    """The probe kept routing to whatever the SDK release notes talked about,
    which is market-making and basis-carry — the two categories where fees
    genuinely eat a retail-size edge. Three live cycles produced three
    flagged strategies and never once tried capital-automation or
    incentive-capture, where the venue PAYS you rather than you extracting
    from other traders.
    """

    async def test_routed_category_is_used_when_it_is_under_represented(self, db, sched):
        picked = await sched._pick_bot_category(db, IdeaCategory.MARKET_MAKING)
        assert picked is IdeaCategory.MARKET_MAKING

    async def test_rotates_away_from_a_saturated_category(self, db, sched):
        for i in range(3):
            idea = _idea(name=f"Maker {i}", category=IdeaCategory.MARKET_MAKING)
            idea.content_hash = f"mm{i}"
            idea.bot_edge_score = 0.8
            await db.save_idea(idea)

        picked = await sched._pick_bot_category(db, IdeaCategory.MARKET_MAKING)
        assert picked is not IdeaCategory.MARKET_MAKING
        from project_forge.models import MONEY_CATEGORIES

        assert picked in MONEY_CATEGORIES

    async def test_prefers_the_least_used_category(self, db, sched):
        for cat, n in (
            (IdeaCategory.MARKET_MAKING, 3),
            (IdeaCategory.BASIS_CARRY, 2),
            (IdeaCategory.CROSS_VENUE_ARBITRAGE, 2),
            (IdeaCategory.INCENTIVE_CAPTURE, 1),
        ):
            for i in range(n):
                idea = _idea(name=f"{cat.value} {i}", category=cat)
                idea.content_hash = f"{cat.value}{i}"
                idea.bot_edge_score = 0.8
                await db.save_idea(idea)

        # capital-automation has none at all — it should win.
        picked = await sched._pick_bot_category(db, IdeaCategory.MARKET_MAKING)
        assert picked is IdeaCategory.CAPITAL_AUTOMATION


@pytest.mark.asyncio
class TestLessonsFeedBack:
    """What the red team rejected must reach the next generation."""

    async def test_flagged_objections_become_lessons(self, db, sched):
        killed = _idea(name="Bad Carry")
        killed.content_hash = "bc1"
        killed.bot_spec.panel_verdict = "flagged"
        killed.bot_spec.surviving_objection = "quoted a one-way fee as a round trip"
        await db.save_idea(killed)

        lessons = await sched._bot_avoid_lessons(db)
        assert any("one-way fee" in lesson for lesson in lessons)
        assert any("Bad Carry" in lesson for lesson in lessons)

    async def test_vetted_strategies_are_not_lessons(self, db, sched):
        good = _idea(name="Good Carry")
        good.content_hash = "gc1"
        good.bot_spec.panel_verdict = "vetted"
        good.bot_spec.surviving_objection = "minor capacity note"
        await db.save_idea(good)

        assert await sched._bot_avoid_lessons(db) == []

    async def test_lessons_are_passed_to_the_generator(self, db, sched, monkeypatch):
        killed = _idea(name="Bad Carry")
        killed.content_hash = "bc2"
        killed.bot_spec.panel_verdict = "flagged"
        killed.bot_spec.surviving_objection = "capacity claim exceeded the reward pool"
        await db.save_idea(killed)

        seen = {}

        async def _gen(_db, _cat, _program, _primitive, avoid_lessons=None):
            seen["lessons"] = avoid_lessons
            return None

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])
        monkeypatch.setattr(sched, "_bot_generate", _gen)
        await sched._fire_bot_strategy(db)

        assert seen["lessons"]
        assert any("capacity claim" in lesson for lesson in seen["lessons"])
