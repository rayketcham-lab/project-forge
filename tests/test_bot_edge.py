"""The money board's axis and its admission gate.

`bot_edge_score` asks a different question from every other axis in the
engine. Not "can we sell it" (fundability), not "how soon is the first
invoice" (cashflow) — but:

    does this edge survive fees, competition and capacity, and can a bot
    run it unattended without anyone getting hurt or breaking a rule

It doubles as the gate, like pki_urgency_score. A generic "AI-powered
trading platform" must score low and be refused; a strategy that names its
venue, its API calls, where the money comes from, and when to switch off
must get through.

The hard veto matters most. Anything that only works by manipulating a
market, trading on non-public information, or exploiting a bug is refused
outright regardless of score — and, because the honest way to describe a
strategy includes saying "no spoofing", a NEGATED mention must not trip it.
"""

from __future__ import annotations

import pytest

from project_forge.engine import bot_edge
from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory


def _spec(**over) -> BotSpec:
    base = dict(
        venue="Polymarket",
        venue_url="https://docs.polymarket.com/rewards",
        family=BotVenueFamily.PREDICTION_MARKETS,
        api_primitives=["CLOB REST order placement", "websocket book feed"],
        mechanism="Venue pays a published per-minute liquidity reward for resting two-sided quotes.",
        capital_floor_usd=500.0,
        capital_target_usd=10000.0,
        expected_return="Pro-rata share of the published reward budget",
        edge_decay="Reward pool is fixed and split pro-rata — yield falls as makers arrive",
        kill_criteria=["reward per minute falls below fees plus adverse selection"],
        validation_plan=["one book, floor capital, 14 days"],
        legality_note="Public venue program with published rules",
        human_touchpoints="Weekly book review",
    )
    base.update(over)
    return BotSpec(**base)


def _idea(**over) -> Idea:
    base = dict(
        name="Reward-minute maker",
        tagline="Rest two-sided quotes inside the max spread and collect the reward budget.",
        description=(
            "A bot that quotes both sides of high-reward Polymarket books via the CLOB REST API, "
            "keeping size resting inside the qualifying spread so it earns liquidity reward "
            "minutes. Income is the venue's published reward budget, split pro-rata among makers, "
            "so yield decays as competitors arrive. Runs unattended with a kill switch on "
            "inventory."
        ),
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis="Reward budgets are published per market; most books have few makers.",
        feasibility_score=0.7,
        mvp_scope="Quote one book at $500 with a hard inventory kill switch.",
        tech_stack=["Python", "websockets"],
    )
    base.update(over)
    idea = Idea(**base)
    if "bot_spec" not in over:
        idea.bot_spec = _spec()
    return idea


# --------------------------------------------------------------------------- #
# Venue extraction                                                            #
# --------------------------------------------------------------------------- #


class TestVenueExtraction:
    def test_prefers_the_spec(self):
        idea = _idea(bot_spec=_spec(venue="Hyperliquid"))
        assert bot_edge.extract_venue(idea) == "Hyperliquid"

    def test_falls_back_to_prose(self):
        idea = _idea(bot_spec=None)
        assert bot_edge.extract_venue(idea) == "Polymarket"

    def test_none_when_no_venue_anywhere(self):
        idea = _idea(
            bot_spec=None,
            name="Generic trading helper",
            tagline="A platform for traders",
            description="It helps people trade better using smart signals.",
            market_analysis="Traders want an edge.",
            mvp_scope="Build a dashboard.",
        )
        assert bot_edge.extract_venue(idea) is None


# --------------------------------------------------------------------------- #
# Heuristic signals                                                           #
# --------------------------------------------------------------------------- #


class TestHeuristic:
    def test_stays_in_range_for_everything(self):
        for idea in (
            _idea(),
            _idea(bot_spec=None),
            _idea(description="x"),
        ):
            score = bot_edge.score_bot_edge_heuristic(idea)
            assert 0.0 <= score <= 1.0

    def test_a_complete_strategy_scores_well(self):
        assert bot_edge.score_bot_edge_heuristic(_idea()) >= bot_edge.BOT_ADMIT_THRESHOLD

    def test_missing_api_surface_scores_lower(self):
        full = bot_edge.score_bot_edge_heuristic(_idea())
        thin = bot_edge.score_bot_edge_heuristic(
            _idea(
                bot_spec=None,
                description="A bot that makes money on prediction markets somehow.",
                mvp_scope="Trade well.",
            )
        )
        assert thin < full

    def test_product_pitch_is_penalised(self):
        """The exact shape the old board produced must not rank here."""
        product = _idea(
            bot_spec=None,
            name="TradeDash",
            tagline="A SaaS dashboard for retail traders",
            description=(
                "A subscription SaaS platform where customers pay $29/month per seat for a "
                "dashboard that shows their portfolio analytics and sends alerts."
            ),
            market_analysis="Retail traders are underserved by existing dashboards.",
            mvp_scope="Build the dashboard and a Stripe subscription flow.",
        )
        assert bot_edge.score_bot_edge_heuristic(product) < bot_edge.BOT_ADMIT_THRESHOLD

    def test_handwave_alpha_is_penalised(self):
        vague = _idea(
            bot_spec=None,
            description=(
                "An AI-powered trading bot using a proprietary machine learning algorithm that "
                "predicts market direction with revolutionary accuracy."
            ),
            mvp_scope="Train the model.",
        )
        assert bot_edge.score_bot_edge_heuristic(vague) < bot_edge.BOT_ADMIT_THRESHOLD

    def test_free_lunch_claim_is_penalised(self):
        honest = bot_edge.score_bot_edge_heuristic(_idea())
        hyped = bot_edge.score_bot_edge_heuristic(
            _idea(
                description=_idea().description + " This is a risk-free guaranteed return.",
            )
        )
        assert hyped < honest

    def test_no_decay_story_scores_below_one_with_it(self):
        with_decay = bot_edge.score_bot_edge_heuristic(_idea())
        without = bot_edge.score_bot_edge_heuristic(
            _idea(
                bot_spec=_spec(edge_decay="n/a"),
                description=(
                    "A bot that quotes both sides of Polymarket books via the CLOB REST API "
                    "and earns the published reward budget."
                ),
            )
        )
        assert without <= with_decay


# --------------------------------------------------------------------------- #
# The hard veto                                                               #
# --------------------------------------------------------------------------- #


class TestIllegalVeto:
    @pytest.mark.parametrize(
        "text",
        [
            "The bot places and cancels orders it never intends to fill to move the price — spoofing the book.",
            "It engages in wash trading between two accounts to manufacture qualifying volume.",
            "It front-runs pending user transactions in the mempool for guaranteed profit.",
            "It trades on non-public information from an insider at the exchange.",
            "It exploits a rounding bug in the vault contract to withdraw more than deposited.",
            "It uses hundreds of sybil accounts to farm the airdrop allocation.",
            "It bypasses KYC by routing through nominee accounts.",
        ],
    )
    def test_manipulative_strategies_are_vetoed(self, text):
        idea = _idea(description=text)
        assert bot_edge.illegal_reason(idea) is not None
        assert bot_edge.score_bot_edge_heuristic(idea) == 0.0
        admitted, reason = bot_edge.admits(idea, 0.9)
        assert not admitted
        assert "legitimate" in reason.lower()

    @pytest.mark.parametrize(
        "text",
        [
            "Provides genuine two-sided liquidity with no spoofing and no orders intended not to trade.",
            "Manufacturing volume through self-matching would be wash trading and is explicitly out of scope.",
            "The design avoids front-running entirely — it only takes publicly displayed prices.",
            "No insider information is used; every input is a public feed.",
        ],
    )
    def test_negated_mentions_do_not_trip_the_veto(self, text):
        """Describing what you refuse to do is honesty, not intent."""
        idea = _idea(description=_idea().description + " " + text)
        assert bot_edge.illegal_reason(idea) is None
        assert bot_edge.score_bot_edge_heuristic(idea) > 0.0


# --------------------------------------------------------------------------- #
# Admission gate                                                              #
# --------------------------------------------------------------------------- #


class TestGate:
    def test_admits_a_complete_strategy(self):
        idea = _idea()
        admitted, reason = bot_edge.admits(idea, 0.8)
        assert admitted, reason

    def test_rejects_wrong_board(self):
        idea = _idea(category=IdeaCategory.SECURITY_TOOL)
        admitted, reason = bot_edge.admits(idea, 0.9)
        assert not admitted
        assert "categ" in reason.lower()

    def test_rejects_missing_spec(self):
        idea = _idea(bot_spec=None)
        admitted, reason = bot_edge.admits(idea, 0.9)
        assert not admitted
        assert "spec" in reason.lower()

    def test_rejects_below_threshold(self):
        idea = _idea()
        admitted, reason = bot_edge.admits(idea, 0.10)
        assert not admitted
        assert "threshold" in reason.lower()

    def test_rejects_spec_without_a_venue_url_or_validation(self):
        """A strategy nobody can go check is not admissible."""
        idea = _idea(bot_spec=_spec(venue_url=None, validation_plan=[]))
        admitted, reason = bot_edge.admits(idea, 0.9)
        assert not admitted
        assert "verif" in reason.lower() or "validat" in reason.lower()


# --------------------------------------------------------------------------- #
# LLM band                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestLlmBand:
    async def test_keyless_keeps_the_heuristic(self, monkeypatch):
        monkeypatch.setattr(bot_edge, "resolve_cheap_backend", lambda: None)
        idea = _idea()
        assert await bot_edge.score_bot_edge(idea) == bot_edge.score_bot_edge_heuristic(idea)

    async def test_borderline_uses_the_backend(self, monkeypatch):
        class _Backend:
            name = "fake"

            def call(self, prompt: str) -> str:
                assert "venue" in prompt.lower()
                return '{"score": 0.61}'

        monkeypatch.setattr(bot_edge, "resolve_cheap_backend", lambda: _Backend())
        monkeypatch.setattr(bot_edge, "score_bot_edge_heuristic", lambda _i: 0.50)
        assert await bot_edge.score_bot_edge(_idea()) == pytest.approx(0.61)

    async def test_unparseable_reply_falls_back(self, monkeypatch):
        class _Backend:
            name = "fake"

            def call(self, prompt: str) -> str:
                return "I think it's pretty good honestly"

        monkeypatch.setattr(bot_edge, "resolve_cheap_backend", lambda: _Backend())
        monkeypatch.setattr(bot_edge, "score_bot_edge_heuristic", lambda _i: 0.50)
        assert await bot_edge.score_bot_edge(_idea()) == pytest.approx(0.50)

    async def test_vetoed_idea_never_reaches_the_backend(self, monkeypatch):
        called = []

        class _Backend:
            name = "fake"

            def call(self, prompt: str) -> str:
                called.append(prompt)
                return '{"score": 0.99}'

        monkeypatch.setattr(bot_edge, "resolve_cheap_backend", lambda: _Backend())
        idea = _idea(description="It wash trades between two accounts to inflate volume.")
        assert await bot_edge.score_bot_edge(idea) == 0.0
        assert not called


# --------------------------------------------------------------------------- #
# Back-fill                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestBackfill:
    async def test_only_scores_gated_bot_ideas(self, db, monkeypatch):
        monkeypatch.setattr(bot_edge, "resolve_cheap_backend", lambda: None)

        gated = _idea(name="Gated", tagline="t1")
        gated.generation_mode = "bot"
        gated.content_hash = "b1"

        drifted = _idea(name="Drifted", tagline="t2")
        drifted.generation_mode = "novel"
        drifted.content_hash = "b2"

        off_board = _idea(name="Off Board", tagline="t3", category=IdeaCategory.SECURITY_TOOL)
        off_board.generation_mode = "bot"
        off_board.content_hash = "b3"

        for idea in (gated, drifted, off_board):
            await db.save_idea(idea)

        report = await bot_edge.score_pending_bot_edge(db, limit=10)
        assert report["scored"] == 1
        assert (await db.get_idea(gated.id)).bot_edge_score is not None
        assert (await db.get_idea(drifted.id)).bot_edge_score is None
        assert (await db.get_idea(off_board.id)).bot_edge_score is None
