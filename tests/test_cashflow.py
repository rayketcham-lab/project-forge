"""Tests for the v0.20 Cashflow board — folding-cash ideas.

A 5th board that surfaces capital-light project ideas with the shortest
path to actual dollars — productized services, digital products, lean
commerce ops (dropshipping done honestly), lead generation, and
flipping/arbitrage — ranked by a NEW axis, `cashflow_score`.

Where fundability asks "can we sell it as a product" (recurring-SaaS
bias), cashflow asks "how fast does this turn into folding cash, with
how little capital". Follows the ambition_score precedent: new nullable
column, standalone engine module (heuristic + borderline-LLM refine,
keyless-safe), scoped back-fill cadence.

Covers:
  - enum membership + CASHFLOW_CATEGORIES grouping (disjoint from
    money / claude-lab / crypto)
  - seeds (saturation standard: >=20 concepts / >=12 domains), personas,
    tech stacks, category bonus
  - the corpus guard: honest hustle, not get-rich-quick scam shapes
  - cashflow_score heuristic: fast-money / low-capital / built-in-demand /
    direct-payment bumps, venture-slow penalty, clamp
  - LLM borderline band + keyless fallback
  - DB round-trip + scoped back-fill idempotence
  - scheduler cadence registration
  - routes: /cashflow page, /api/cashflow/top, churn lab=cashflow
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.llm_generator import PERSONAS_BY_CATEGORY
from project_forge.models import (
    CASHFLOW_CATEGORIES,
    CLAUDE_LAB_CATEGORIES,
    CRYPTO_CATEGORIES,
    MONEY_CATEGORIES,
    Idea,
    IdeaCategory,
)
from project_forge.storage.db import Database

NEW_CASHFLOW = (
    IdeaCategory.PRODUCTIZED_SERVICES,
    IdeaCategory.DIGITAL_PRODUCTS,
    IdeaCategory.COMMERCE_OPS,
    IdeaCategory.LEAD_GENERATION,
    IdeaCategory.FLIPPING_ARBITRAGE,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "cashflow.db")
    await database.connect()
    yield database
    await database.close()


def _cash_idea(**over) -> Idea:
    """Neutral builder — no cashflow trigger words, so each heuristic test
    controls exactly which signals are present."""
    base = dict(
        name="Cert Expiry Audit Service",
        tagline="a packaged security review for small teams",
        description="A focused offering that solves one sharp problem for one buyer.",
        category=IdeaCategory.PRODUCTIZED_SERVICES,
        market_analysis="Small firms need this and budget for it annually.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: the core engine. Phase 2: the client portal.",
        tech_stack=["python", "fastapi"],
    )
    base.update(over)
    return Idea(**base)


def _loaded_idea(**over) -> Idea:
    """Every fast-cash signal at once — the folding-cash archetype."""
    base = dict(
        name="Fixed-Price PKI Audit",
        tagline="fixed-price certificate-expiry audit delivered this week",
        description=(
            "A productized certificate-expiry audit: the scanner runs "
            "automatically, the client pays a 50% deposit upfront via payment "
            "link, and the branded report ships within days. No inventory and "
            "no ad spend — sold through Upwork and direct referrals."
        ),
        category=IdeaCategory.PRODUCTIZED_SERVICES,
        market_analysis="Compliance-driven buyers with annual budget; repeat engagements.",
        feasibility_score=0.8,
        mvp_scope="Phase 1: scanner + branded PDF. Invoice on delivery.",
        tech_stack=["python", "fastapi", "stripe"],
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# grouping                                                                    #
# --------------------------------------------------------------------------- #


class TestCashflowGrouping:
    def test_new_categories_in_enum(self):
        for cat in NEW_CASHFLOW:
            assert isinstance(cat, IdeaCategory)

    def test_grouping_is_exactly_the_new_set(self):
        assert set(CASHFLOW_CATEGORIES) == set(NEW_CASHFLOW)

    def test_disjoint_from_other_boards(self):
        assert not (set(CASHFLOW_CATEGORIES) & set(MONEY_CATEGORIES))
        assert not (set(CASHFLOW_CATEGORIES) & set(CLAUDE_LAB_CATEGORIES))
        assert not (set(CASHFLOW_CATEGORIES) & set(CRYPTO_CATEGORIES))

    def test_route_tuple_matches_canonical(self):
        # Import app first so routes loads via the normal path (avoids the
        # pre-existing app<->routes circular import on cold collection).
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._CASHFLOW_CATEGORIES) == {c.value for c in CASHFLOW_CATEGORIES}


# --------------------------------------------------------------------------- #
# seeds / personas / stacks / bonus                                           #
# --------------------------------------------------------------------------- #


class TestCashflowSeedsAndWiring:
    def test_new_categories_meet_saturation_standard(self):
        # The saturation fix (test_idea_saturation_fix.py) enforces >=20
        # concepts and >=12 domains for every category — meet it from day one.
        for cat in NEW_CASHFLOW:
            assert cat in CATEGORY_SEEDS, f"missing seeds for {cat}"
            seeds = CATEGORY_SEEDS[cat]
            assert len(seeds["seed_concepts"]) >= 20
            assert len(seeds["domains_to_cross"]) >= 12
            assert len(seeds["seed_concepts"]) == len(set(seeds["seed_concepts"]))

    def test_new_categories_have_personas(self):
        for cat in NEW_CASHFLOW:
            assert cat in PERSONAS_BY_CATEGORY, f"missing personas for {cat}"
            assert len(PERSONAS_BY_CATEGORY[cat]) >= 5

    def test_new_categories_have_tech_stacks(self):
        from project_forge.cron.auto_scan import TECH_STACKS

        for cat in NEW_CASHFLOW:
            assert cat in TECH_STACKS, f"missing tech stacks for {cat}"
            assert len(TECH_STACKS[cat]) >= 3

    def test_new_categories_have_cashflow_bonus(self):
        from project_forge.engine.cashflow import _CATEGORY_BONUS

        for cat in NEW_CASHFLOW:
            assert _CATEGORY_BONUS.get(cat, 0.0) > 0.0


class TestCashflowBoardIsHonestHustle:
    """The board's identity: honest folding-cash systems — services,
    products, ops, leads, flips — never get-rich-quick scam shapes."""

    def _corpus(self) -> str:
        parts: list[str] = []
        for cat in CASHFLOW_CATEGORIES:
            seeds = CATEGORY_SEEDS[cat]
            parts.append(seeds["description"])
            parts.extend(seeds["seed_concepts"])
        return " ".join(parts).lower()

    def test_corpus_covers_folding_cash_themes(self):
        corpus = self._corpus()
        for theme in ("dropship", "fixed-price", "template", "lead", "flip"):
            assert theme in corpus, f"cashflow board missing theme: {theme}"

    def test_corpus_is_not_get_rich_quick(self):
        corpus = self._corpus()
        for banned in ("mlm", "pyramid", "get rich", "casino", "gambling", "binary options"):
            assert banned not in corpus, f"cashflow board drifted to scam shape: {banned}"


# --------------------------------------------------------------------------- #
# heuristic                                                                   #
# --------------------------------------------------------------------------- #


class TestCashflowHeuristic:
    def test_loaded_idea_scores_high(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        assert score_cashflow_heuristic(_loaded_idea()) >= 0.65

    def test_venture_shaped_idea_scores_low(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        venture = _cash_idea(
            name="Two-Sided Platform",
            category=IdeaCategory.LEAD_GENERATION,
            description=(
                "A two-sided platform play that needs to raise a seed round and "
                "burn 18 months of runway building network effects before any "
                "monetization is possible."
            ),
            market_analysis="Requires venture funding to reach critical mass.",
            mvp_scope="Build the platform.",
        )
        s = score_cashflow_heuristic(venture)
        assert s <= 0.35
        assert s < score_cashflow_heuristic(_loaded_idea())

    def test_direct_payment_mechanics_bump(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        without = score_cashflow_heuristic(_cash_idea())
        with_pay = score_cashflow_heuristic(_cash_idea(description="Clients pay a deposit and an invoice at kickoff."))
        assert with_pay > without

    def test_low_capital_bump(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        without = score_cashflow_heuristic(_cash_idea())
        with_cap = score_cashflow_heuristic(_cash_idea(description="No inventory and no ad spend required to start."))
        assert with_cap > without

    def test_builtin_demand_marketplace_bump(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        without = score_cashflow_heuristic(_cash_idea())
        with_mkt = score_cashflow_heuristic(
            _cash_idea(description="Sold on Etsy and Gumroad storefronts from day launch.")
        )
        assert with_mkt > without

    def test_score_clamped_to_unit_interval(self):
        from project_forge.engine.cashflow import score_cashflow_heuristic

        s = score_cashflow_heuristic(_loaded_idea(category=IdeaCategory.PRODUCTIZED_SERVICES))
        assert 0.0 <= s <= 1.0


class TestCashflowLLMBand:
    @pytest.mark.asyncio
    async def test_borderline_pulls_llm_score(self, monkeypatch):
        from project_forge.engine import cashflow

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value='{"score": 0.62}')
        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: backend)

        # Neutral productized idea + payment mechanics ≈ 0.45 — in the band.
        idea = _cash_idea(description="Clients pay a deposit and an invoice at kickoff.")
        score = await cashflow.score_cashflow(idea)
        assert abs(score - 0.62) < 0.05
        backend.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_backend_falls_back_to_heuristic(self, monkeypatch):
        from project_forge.engine import cashflow

        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: None)
        idea = _cash_idea(description="Clients pay a deposit and an invoice at kickoff.")
        score = await cashflow.score_cashflow(idea)
        assert score == cashflow.score_cashflow_heuristic(idea)
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_clear_low_skips_llm(self, monkeypatch):
        from project_forge.engine import cashflow

        backend = MagicMock()
        backend.call = MagicMock(return_value='{"score": 0.99}')
        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: backend)

        # No cashflow-category bonus, no signals — clearly below the band.
        idea = _cash_idea(category=IdeaCategory.OBSERVABILITY)
        score = await cashflow.score_cashflow(idea)
        assert score < 0.35
        backend.call.assert_not_called()


# --------------------------------------------------------------------------- #
# DB round-trip + back-fill                                                   #
# --------------------------------------------------------------------------- #


class TestDbRoundTrip:
    @pytest.mark.asyncio
    async def test_cashflow_score_persists(self, db):
        idea = _cash_idea(content_hash="rt1")
        idea.cashflow_score = 0.77
        await db.save_idea(idea)
        got = await db.get_idea(idea.id)
        assert got is not None
        assert got.cashflow_score == 0.77


class TestBackfill:
    @pytest.mark.asyncio
    async def test_scores_only_cashflow_categories(self, db, monkeypatch):
        from project_forge.engine import cashflow

        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: None)
        a = _cash_idea(name="Cash A", content_hash="bf1")
        b = _cash_idea(
            name="Not Cash",
            category=IdeaCategory.MICRO_SAAS,
            content_hash="bf2",
        )
        await db.save_idea(a)
        await db.save_idea(b)

        report = await cashflow.score_pending_cashflow(db, limit=10)
        assert report["scored"] == 1

        got_a = await db.get_idea(a.id)
        got_b = await db.get_idea(b.id)
        assert got_a.cashflow_score is not None
        assert got_b.cashflow_score is None

    @pytest.mark.asyncio
    async def test_backfill_is_idempotent(self, db, monkeypatch):
        from project_forge.engine import cashflow

        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: None)
        await db.save_idea(_cash_idea(name="Once", content_hash="bf3"))
        first = await cashflow.score_pending_cashflow(db, limit=10)
        second = await cashflow.score_pending_cashflow(db, limit=10)
        assert first["scored"] == 1
        assert second["scored"] == 0

    @pytest.mark.asyncio
    async def test_backfill_respects_limit(self, db, monkeypatch):
        from project_forge.engine import cashflow

        monkeypatch.setattr(cashflow, "resolve_cheap_backend", lambda: None)
        for i in range(4):
            await db.save_idea(_cash_idea(name=f"Lim {i}", content_hash=f"bf-l{i}"))
        report = await cashflow.score_pending_cashflow(db, limit=2)
        assert report["scored"] == 2


# --------------------------------------------------------------------------- #
# scheduler                                                                   #
# --------------------------------------------------------------------------- #


class TestCadence:
    def test_cashflow_backfill_cadence_registered(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        names = {c.name for c in default_cadences()}
        assert "cashflow_score" in names


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "test_cashflow_routes.db"
    await db.connect()
    # Two cashflow-scored ideas + one unscored that must NOT surface.
    a = _cash_idea(name="Cash High", content_hash="ra")
    a.cashflow_score = 0.88
    a.generation_mode = "novel"
    b = _cash_idea(
        name="Cash Low",
        category=IdeaCategory.FLIPPING_ARBITRAGE,
        content_hash="rb",
    )
    b.cashflow_score = 0.41
    b.generation_mode = "inversion"
    c = _cash_idea(name="Cash Unscored", content_hash="rc")  # no cashflow_score
    await db.save_idea(a)
    await db.save_idea(b)
    await db.save_idea(c)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestCashflowRoutes:
    @pytest.mark.asyncio
    async def test_api_top_returns_only_scored_sorted(self, client):
        resp = await client.get("/api/cashflow/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        names = [d["name"] for d in data]
        assert names == ["Cash High", "Cash Low"]  # sorted desc, unscored excluded
        assert data[0]["cashflow_score"] == 0.88
        assert data[0]["category"] == "productized-services"

    @pytest.mark.asyncio
    async def test_page_renders_scored_ideas(self, client):
        resp = await client.get("/cashflow")
        assert resp.status_code == 200
        html = resp.text
        assert "Cash High" in html
        assert "Cash Unscored" not in html

    @pytest.mark.asyncio
    async def test_category_filter_narrows(self, client):
        resp = await client.get("/cashflow?category=flipping-arbitrage")
        html = resp.text
        assert "Cash Low" in html
        assert "Cash High" not in html

    @pytest.mark.asyncio
    async def test_churn_cashflow_path(self, client, monkeypatch):
        """lab=cashflow routes through generate_idea_llm + score_cashflow + save."""
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult

        idea = _cash_idea(
            name="Churned Cash",
            tagline="marketplace mispricing scanner with same-day resale comps",
            description=(
                "A distinct flipping tool: scans local listings against resale "
                "comps and flags the underpriced ones the moment they post, "
                "with fee-true margin per item."
            ),
            category=IdeaCategory.FLIPPING_ARBITRAGE,
            content_hash="churn-cash-1",
        )
        idea.generation_mode = "novel"

        async def _fake_generate(db_, category, **kw):
            return LLMGenerationResult(
                idea=idea,
                mode="novel",
                persona="p",
                backend="stub",
                raw_response="{}",
            )

        monkeypatch.setattr(gen, "generate_idea_llm", _fake_generate)
        resp = await client.post("/api/churn", json={"lab": "cashflow"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"] is not None
        assert data["idea"]["name"] == "Churned Cash"
        assert data["idea"]["category"] == "flipping-arbitrage"
        assert isinstance(data["idea"]["cashflow_score"], float)
