"""v0.24 Money Bots rework — capital-deployment strategies, not products.

The board named "money bots" had no generator of its own: it was /explore
filtered to eight product categories and sorted by fundability. So it
produced SaaS pitches, never a bot that puts capital to work.

This suite pins the new object. A money bot is a STRATEGY:

    a named venue, the API primitives the bot actually calls, the
    mechanism the yield comes from, the capital it needs, how the edge
    decays, and when to switch it off

Phase 1 here: the model layer — the five capital-deployment categories,
the BotSpec contract, and persistence. Later phases add the scoring axis,
the admission gate, the grounded venue probe, and the board.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from project_forge.models import (
    CASHFLOW_CATEGORIES,
    CLAUDE_LAB_CATEGORIES,
    CRYPTO_CATEGORIES,
    MONEY_CATEGORIES,
    PKI_CATEGORIES,
    PRODUCT_MONEY_CATEGORIES,
    SNIPER_CATEGORIES,
    BotSpec,
    BotVenueFamily,
    Idea,
    IdeaCategory,
)

NEW_BOT = (
    IdeaCategory.MARKET_MAKING,
    IdeaCategory.INCENTIVE_CAPTURE,
    IdeaCategory.CROSS_VENUE_ARBITRAGE,
    IdeaCategory.BASIS_CARRY,
    IdeaCategory.CAPITAL_AUTOMATION,
)

OLD_PRODUCT = (
    IdeaCategory.AUTOMATION_INCOME,
    IdeaCategory.CREATOR_TOOLS,
    IdeaCategory.CONSUMER_APP,
    IdeaCategory.PRODUCTIVITY,
    IdeaCategory.MICRO_SAAS,
    IdeaCategory.VERTICAL_SAAS,
    IdeaCategory.ECOMMERCE_TOOLS,
    IdeaCategory.FINTECH_TOOLS,
)


def _spec(**over) -> BotSpec:
    base = dict(
        venue="Polymarket",
        venue_url="https://docs.polymarket.com/rewards",
        family=BotVenueFamily.PREDICTION_MARKETS,
        api_primitives=["CLOB REST order placement", "websocket book feed"],
        mechanism=(
            "Venue pays liquidity rewards per minute for resting two-sided quotes "
            "inside the max spread — income is the reward budget, not the fill P&L."
        ),
        capital_floor_usd=500.0,
        capital_target_usd=10000.0,
        expected_return="Reward budget share; decays as more makers quote the same book",
        edge_decay="Reward pool is fixed and split pro-rata — yield falls as competitors arrive",
        kill_criteria=["reward per minute falls below fee + adverse selection cost"],
        validation_plan=["run one book at floor capital for 14 days, measure realised reward share"],
        legality_note="Public venue program, published rules, no market manipulation",
        human_touchpoints="Weekly review of which books to quote",
    )
    base.update(over)
    return BotSpec(**base)


# --------------------------------------------------------------------------- #
# Categories + board groupings                                                #
# --------------------------------------------------------------------------- #


class TestBotCategories:
    def test_new_categories_in_enum(self):
        for cat in NEW_BOT:
            assert isinstance(cat, IdeaCategory)

    def test_money_categories_is_now_the_bot_set(self):
        """The board named 'money bots' must mean bots that deploy capital."""
        assert set(MONEY_CATEGORIES) == set(NEW_BOT)

    def test_product_categories_preserved_under_new_name(self):
        assert set(PRODUCT_MONEY_CATEGORIES) == set(OLD_PRODUCT)

    def test_bot_and_product_groupings_are_disjoint(self):
        assert not (set(MONEY_CATEGORIES) & set(PRODUCT_MONEY_CATEGORIES))

    def test_disjoint_from_every_other_board(self):
        for other in (CLAUDE_LAB_CATEGORIES, CRYPTO_CATEGORIES, CASHFLOW_CATEGORIES, PKI_CATEGORIES):
            assert not (set(MONEY_CATEGORIES) & set(other))

    def test_sniper_scope_unchanged(self):
        """Sniper hunts product incumbents — it must not inherit the bot set."""
        assert set(OLD_PRODUCT) <= set(SNIPER_CATEGORIES)
        assert not (set(NEW_BOT) & set(SNIPER_CATEGORIES))

    def test_auto_promote_still_targets_products(self):
        from project_forge.cron.auto_promote_runner import _DEFAULT_PROMOTE_CATEGORIES

        assert set(_DEFAULT_PROMOTE_CATEGORIES) == set(OLD_PRODUCT)

    def test_route_tuple_matches_canonical(self):
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._MONEY_CATEGORIES) == {c.value for c in MONEY_CATEGORIES}


# --------------------------------------------------------------------------- #
# BotSpec contract                                                            #
# --------------------------------------------------------------------------- #


class TestBotSpec:
    def test_valid_spec_round_trips(self):
        spec = _spec()
        assert spec.venue == "Polymarket"
        assert spec.family is BotVenueFamily.PREDICTION_MARKETS
        assert BotSpec(**spec.model_dump()) == spec

    def test_venue_is_required(self):
        with pytest.raises(ValidationError):
            _spec(venue="   ")

    def test_needs_at_least_one_api_primitive(self):
        """A strategy with no API surface is a wish, not a bot."""
        with pytest.raises(ValidationError):
            _spec(api_primitives=[])

    def test_needs_a_mechanism(self):
        with pytest.raises(ValidationError):
            _spec(mechanism="")

    def test_capital_floor_cannot_exceed_target(self):
        with pytest.raises(ValidationError):
            _spec(capital_floor_usd=10_000.0, capital_target_usd=500.0)

    def test_capital_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            _spec(capital_floor_usd=-1.0)

    def test_needs_a_kill_criterion(self):
        """Every bot that touches real money must say when to switch it off."""
        with pytest.raises(ValidationError):
            _spec(kill_criteria=[])

    def test_needs_an_edge_decay_story(self):
        with pytest.raises(ValidationError):
            _spec(edge_decay="")

    def test_family_accepts_all_four_venue_universes(self):
        for fam in (
            BotVenueFamily.PREDICTION_MARKETS,
            BotVenueFamily.CRYPTO_DEFI,
            BotVenueFamily.SPORTSBOOK,
            BotVenueFamily.BROKERAGE,
            BotVenueFamily.OTHER,
        ):
            assert _spec(family=fam).family is fam


# --------------------------------------------------------------------------- #
# Idea fields + persistence                                                   #
# --------------------------------------------------------------------------- #


def _idea(**over) -> Idea:
    base = dict(
        name="Polymarket reward-minute maker",
        tagline="Quote both sides inside max spread and collect the reward budget.",
        description="Resting two-sided quotes on high-reward books.",
        category=IdeaCategory.INCENTIVE_CAPTURE,
        market_analysis="Reward pool is published per market and split pro-rata.",
        feasibility_score=0.7,
        mvp_scope="One book, floor capital, paper mode first.",
        tech_stack=["Python", "websockets"],
    )
    base.update(over)
    return Idea(**base)


class TestIdeaBotFields:
    def test_defaults_are_none(self):
        idea = _idea()
        assert idea.bot_edge_score is None
        assert idea.bot_spec is None

    def test_carries_spec_and_score(self):
        idea = _idea(bot_edge_score=0.72, bot_spec=_spec())
        assert idea.bot_edge_score == 0.72
        assert idea.bot_spec.venue == "Polymarket"


@pytest.mark.asyncio
class TestBotPersistence:
    async def test_spec_and_score_survive_a_round_trip(self, db):
        idea = _idea(bot_edge_score=0.66, bot_spec=_spec())
        await db.save_idea(idea)

        loaded = await db.get_idea(idea.id)
        assert loaded is not None
        assert loaded.bot_edge_score == 0.66
        assert loaded.bot_spec is not None
        assert loaded.bot_spec.venue == "Polymarket"
        assert loaded.bot_spec.api_primitives == ["CLOB REST order placement", "websocket book feed"]
        assert loaded.bot_spec.capital_floor_usd == 500.0
        assert loaded.bot_spec.kill_criteria == ["reward per minute falls below fee + adverse selection cost"]

    async def test_ideas_without_a_spec_still_load(self, db):
        idea = _idea()
        await db.save_idea(idea)
        loaded = await db.get_idea(idea.id)
        assert loaded is not None
        assert loaded.bot_spec is None
        assert loaded.bot_edge_score is None

    async def test_corrupt_spec_json_does_not_break_loading(self, db):
        """A hand-edited or truncated row must not take the board down."""
        idea = _idea()
        await db.save_idea(idea)
        await db.db.execute("UPDATE ideas SET bot_spec = ? WHERE id = ?", ("{not json", idea.id))
        await db.db.commit()

        loaded = await db.get_idea(idea.id)
        assert loaded is not None
        assert loaded.bot_spec is None
