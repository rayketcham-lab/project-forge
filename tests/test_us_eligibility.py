"""The money board is US-only: a venue the operator cannot legally use is
not a money-making idea, it is a distraction.

Operator is US-based. The offshore Polymarket CLOB bars US persons — the
CFTC-regulated US entity is a different venue with different mechanics —
and the offshore perp venues (Hyperliquid, Binance, Bybit, OKX) are
geoblocked. Every one of those had already produced a strategy on this
board before this rule existed.

Two design decisions worth stating:

  * ambiguity FAILS CLOSED. A spec that says only "Polymarket" resolves to
    the offshore book and is refused; naming the US entity is the way to
    pass. A bot that trades on a venue the operator is not eligible for is
    the single most expensive mistake this board could ship.
  * the registry records a status and a note, never a legal conclusion.
    "Confirm before funding" stays on every entry, because venue terms and
    state-level availability move and this engine does not track them.
"""

from __future__ import annotations

import pytest

from project_forge.engine import bot_edge
from project_forge.feeds import venue_probe
from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory


def _spec(venue: str, **over) -> BotSpec:
    base = dict(
        venue=venue,
        venue_url="https://example.com/docs",
        family=BotVenueFamily.PREDICTION_MARKETS,
        api_primitives=["REST order placement"],
        mechanism="Venue pays a published liquidity reward for resting quotes.",
        capital_floor_usd=500.0,
        capital_target_usd=5000.0,
        expected_return="Share of the published budget",
        edge_decay="Pool is fixed; yield falls as makers arrive",
        kill_criteria=["reward below fees"],
        validation_plan=["one book, 14 days"],
        legality_note="Published program",
    )
    base.update(over)
    return BotSpec(**base)


def _idea(venue: str) -> Idea:
    idea = Idea(
        name="Test strategy",
        tagline="quotes a book for the published reward",
        description="Rests two-sided quotes via the REST API to earn the reward budget.",
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis="Reward budgets are published per market.",
        feasibility_score=0.7,
        mvp_scope="One book at floor capital.",
        tech_stack=["python"],
    )
    idea.bot_spec = _spec(venue)
    return idea


class TestRegistryStatus:
    def test_every_venue_declares_a_us_status(self):
        for venue in venue_probe.VENUE_REGISTRY:
            assert venue.us_status in {"eligible", "restricted", "verify"}, venue.name

    def test_every_venue_still_says_what_to_confirm(self):
        for venue in venue_probe.VENUE_REGISTRY:
            assert venue.eligibility_note.strip(), venue.name

    def test_offshore_venues_are_restricted(self):
        for name in ("Polymarket", "Hyperliquid", "Betfair"):
            assert venue_probe.venue_us_status(name) == "restricted", name

    def test_us_regulated_venues_are_eligible(self):
        for name in ("Kalshi", "Alpaca", "Interactive Brokers", "Coinbase"):
            assert venue_probe.venue_us_status(name) == "eligible", name

    def test_the_us_entity_is_a_separate_venue_from_the_offshore_book(self):
        assert venue_probe.venue_us_status("Polymarket US") == "eligible"
        assert venue_probe.venue_us_status("Polymarket") == "restricted"

    def test_eligible_venues_helper_excludes_restricted(self):
        eligible = {v.name for v in venue_probe.us_eligible_venues()}
        assert "Kalshi" in eligible
        assert "Polymarket" not in eligible
        assert "Hyperliquid" not in eligible


class TestStatusLookup:
    @pytest.mark.parametrize(
        "text",
        [
            "Polymarket (CLOB)",
            "Polymarket CLOB (Polygon mainnet)",
            "Hyperliquid perpetuals paired against Bybit",
            "Binance USDⓈ-M",
        ],
    )
    def test_free_text_venue_strings_resolve(self, text):
        assert venue_probe.venue_us_status(text) == "restricted"

    def test_unknown_venue_fails_closed(self):
        """An unrecognised venue is not assumed safe."""
        assert venue_probe.venue_us_status("Some New Exchange") == "verify"

    def test_us_entity_wins_over_the_bare_name(self):
        assert venue_probe.venue_us_status("Polymarket US (CFTC-regulated)") == "eligible"


class TestGate:
    def test_restricted_venue_is_refused(self):
        admitted, reason = bot_edge.admits(_idea("Polymarket (CLOB)"), 0.95)
        assert not admitted
        assert "us" in reason.lower()

    def test_eligible_venue_passes(self):
        admitted, reason = bot_edge.admits(_idea("Kalshi"), 0.95)
        assert admitted, reason

    def test_unknown_venue_is_refused_pending_verification(self):
        admitted, reason = bot_edge.admits(_idea("Some New Exchange"), 0.95)
        assert not admitted
        assert "verif" in reason.lower() or "us" in reason.lower()


class TestSeed:
    def _program(self) -> dict:
        return {
            "venue": "Kalshi",
            "family": BotVenueFamily.PREDICTION_MARKETS.value,
            "category": IdeaCategory.INCENTIVE_CAPTURE.value,
            "title": "rewards",
            "url": "https://example.com/x",
            "summary": "reward budget",
            "source": "github-issue",
            "program_score": 5,
        }

    def test_seed_states_the_us_requirement(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "United States" in seed or "US-based" in seed
        assert "eligible" in seed.lower()

    def test_seed_lists_venues_that_qualify(self):
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "Kalshi" in seed

    def test_seed_names_the_polymarket_trap(self):
        """The exact mistake that reached the board: the offshore book."""
        seed = venue_probe.program_to_seed(self._program(), primitive=None)
        assert "Polymarket US" in seed


class TestProbeScope:
    def test_probe_only_sweeps_venues_the_operator_can_use(self):
        for _repo, venue_name in venue_probe.PROBE_REPOS:
            assert venue_probe.venue_us_status(venue_name) != "restricted", venue_name


@pytest.mark.asyncio
class TestBackfill:
    """Strategies stored before the US rule existed must be re-judged.

    Three vetted strategies were sitting on the board naming the offshore
    Polymarket CLOB — exactly what the operator was about to reject by
    hand. Adding the rule without re-running it over what is already
    stored would leave the board advertising ineligible venues.
    """

    async def _store(self, db, name: str, venue: str):
        idea = _idea(venue)
        idea.name = name
        idea.content_hash = name
        idea.generation_mode = "bot"
        idea.bot_edge_score = 0.9
        idea.bot_spec.panel_verdict = "vetted"
        await db.save_idea(idea)
        return idea

    async def test_restricted_venues_are_reverdicted(self, db):
        from project_forge.engine.bot_edge import reverdict_us_eligibility

        offshore = await self._store(db, "Offshore Maker", "Polymarket (CLOB)")
        onshore = await self._store(db, "Onshore Maker", "Kalshi")

        report = await reverdict_us_eligibility(db)
        assert report["reverdicted"] == 1

        assert (await db.get_idea(offshore.id)).bot_spec.panel_verdict == "us-ineligible"
        assert (await db.get_idea(onshore.id)).bot_spec.panel_verdict == "vetted"

    async def test_it_is_idempotent(self, db):
        from project_forge.engine.bot_edge import reverdict_us_eligibility

        await self._store(db, "Offshore Maker", "Hyperliquid perps")
        assert (await reverdict_us_eligibility(db))["reverdicted"] == 1
        assert (await reverdict_us_eligibility(db))["reverdicted"] == 0
