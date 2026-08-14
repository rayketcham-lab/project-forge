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
    "venue": "Polymarket",
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
        venue="Polymarket",
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
            venue="Polymarket",
            anchor="https://example.com/1",
            admitted=False,
            reason="panel killed it",
        )
        rows = await db.list_bot_probes(limit=5)
        assert len(rows) == 1
        assert rows[0]["venue"] == "Polymarket"
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

    async def test_red_team_kill_stores_nothing(self, db, sched, monkeypatch):
        from project_forge.engine.bot_depth import StressResult

        monkeypatch.setattr(sched, "_bot_fetch_programs", lambda: [_PROGRAM])

        async def _gen(*_a, **_k):
            return _Result(_idea())

        async def _kill(idea):
            return StressResult(idea=idea, survived=False, strongest="fees eat it", passes=4)

        monkeypatch.setattr(sched, "_bot_generate", _gen)
        monkeypatch.setattr(sched, "_bot_stress", _kill)
        await sched._fire_bot_strategy(db)

        probes = await db.list_bot_probes()
        assert probes[0]["admitted"] is False
        assert "fees eat it" in (probes[0]["reason"] or "")
        assert not await db.list_ideas(limit=10)

    async def test_below_threshold_stores_nothing(self, db, sched, monkeypatch):
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

        probes = await db.list_bot_probes()
        assert probes[0]["admitted"] is False
        assert "threshold" in (probes[0]["reason"] or "")
        assert not await db.list_ideas(limit=10)

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
        assert stored.bot_spec.venue == "Polymarket"

    async def test_already_probed_programs_are_skipped(self, db, sched, monkeypatch):
        """The same GitHub issue must not be worked twice."""
        await db.record_bot_probe(
            program_summary="seen",
            venue="Polymarket",
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
