"""Tests for the v0.19 Crypto / Web3 money-idea board.

A grouping board (same shape as Money Bots) that surfaces *fundable*
on-chain opportunities — smart-contract security, web3 infrastructure,
DeFi tooling, stablecoin payment rails, and crypto compliance — and
explicitly NOT speculative NFT-art minting. It reuses the
`fundability_score` axis, so there is no schema or scheduler change: new
crypto categories auto-generate on the expand rotation and auto-score on
the fundability back-fill cadence.

Covers:
  - enum membership + CRYPTO_CATEGORIES grouping (disjoint from money/lab)
  - CATEGORY_SEEDS, personas, and fundability bonus per new category
  - the board's fundable-infra intent (guard against art-minting drift)
  - routes: /crypto page, /api/crypto/top filter+sort, churn lab=crypto
  - route tuple derivation matches the canonical grouping
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.engine.categories import CATEGORY_SEEDS
from project_forge.engine.fundability import _CATEGORY_BONUS as FUNDABILITY_BONUS
from project_forge.engine.llm_generator import PERSONAS_BY_CATEGORY
from project_forge.models import (
    CLAUDE_LAB_CATEGORIES,
    CRYPTO_CATEGORIES,
    MONEY_CATEGORIES,
    Idea,
    IdeaCategory,
)
from project_forge.storage.db import Database

NEW_CRYPTO = (
    IdeaCategory.ONCHAIN_SECURITY,
    IdeaCategory.WEB3_INFRA,
    IdeaCategory.DEFI_TOOLING,
    IdeaCategory.STABLECOIN_PAYMENTS,
    IdeaCategory.CRYPTO_COMPLIANCE,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "crypto.db")
    await database.connect()
    yield database
    await database.close()


def _crypto_idea(**over) -> Idea:
    base = dict(
        name="Wallet Firewall",
        tagline="transaction-simulation firewall for wallet approvals",
        description=(
            "Users blind-sign opaque wallet prompts and get drained. This is a "
            "subscription firewall that simulates each signing request and shows "
            "the real balance impact before approval, with a paid team tier."
        ),
        category=IdeaCategory.ONCHAIN_SECURITY,
        market_analysis=(
            "Sold to wallet vendors and custody desks; enterprise budgets, "
            "mandatory spend after every exploit. $99/mo per seat."
        ),
        feasibility_score=0.7,
        mvp_scope="Phase 1 EVM simulation. Phase 2 policy rules. Phase 3 team tier.",
        tech_stack=["python", "fastapi", "stripe"],
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# grouping                                                                    #
# --------------------------------------------------------------------------- #


class TestCryptoGrouping:
    def test_new_categories_in_enum(self):
        for cat in NEW_CRYPTO:
            assert isinstance(cat, IdeaCategory)

    def test_new_categories_grouped_as_crypto(self):
        for cat in NEW_CRYPTO:
            assert cat in CRYPTO_CATEGORIES

    def test_crypto_grouping_is_exactly_the_new_set(self):
        assert set(CRYPTO_CATEGORIES) == set(NEW_CRYPTO)

    def test_crypto_disjoint_from_money_and_lab(self):
        assert not (set(CRYPTO_CATEGORIES) & set(MONEY_CATEGORIES))
        assert not (set(CRYPTO_CATEGORIES) & set(CLAUDE_LAB_CATEGORIES))

    def test_route_tuple_matches_canonical(self):
        # Import app first so routes loads via the normal path; importing
        # routes cold (this file in isolation) trips a pre-existing
        # app<->routes circular import. In the full suite app is already
        # loaded by an earlier test, so this just mirrors reality.
        from project_forge.web import app as _app  # noqa: F401
        from project_forge.web import routes

        assert set(routes._CRYPTO_CATEGORIES) == {c.value for c in CRYPTO_CATEGORIES}


# --------------------------------------------------------------------------- #
# seeds / personas / scoring                                                  #
# --------------------------------------------------------------------------- #


class TestCryptoSeedsAndScoring:
    def test_new_categories_have_well_formed_seeds(self):
        for cat in NEW_CRYPTO:
            assert cat in CATEGORY_SEEDS, f"missing seeds for {cat}"
            seeds = CATEGORY_SEEDS[cat]
            assert len(seeds["seed_concepts"]) >= 5
            assert len(seeds["domains_to_cross"]) >= 3
            assert len(seeds["seed_concepts"]) == len(set(seeds["seed_concepts"]))

    def test_new_categories_have_personas(self):
        for cat in NEW_CRYPTO:
            assert cat in PERSONAS_BY_CATEGORY, f"missing personas for {cat}"
            assert len(PERSONAS_BY_CATEGORY[cat]) >= 5

    def test_new_categories_have_fundability_bonus(self):
        for cat in NEW_CRYPTO:
            assert FUNDABILITY_BONUS.get(cat, 0.0) > 0.0


class TestCryptoBoardIsFundableNotArt:
    """The board's reason to exist: fundable on-chain infra/security/
    payments/compliance — NOT speculative NFT-art minting. Guard the seed
    corpus against drift back toward 'generate JPEGs and sell them'."""

    def _corpus(self) -> str:
        parts: list[str] = []
        for cat in CRYPTO_CATEGORIES:
            seeds = CATEGORY_SEEDS[cat]
            parts.append(seeds["description"])
            parts.extend(seeds["seed_concepts"])
        return " ".join(parts).lower()

    def test_corpus_covers_fundable_themes(self):
        corpus = self._corpus()
        for theme in ("audit", "wallet", "compliance", "payment", "infrastructure"):
            assert theme in corpus, f"crypto board missing fundable theme: {theme}"

    def test_corpus_is_not_art_minting(self):
        corpus = self._corpus()
        for banned in ("nft art", "mint art", "jpeg", "pfp", "art collection"):
            assert banned not in corpus, f"crypto board drifted to art-minting: {banned}"


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "test_crypto_routes.db"
    await db.connect()
    # Two fundability-scored crypto ideas + one unscored that must NOT surface.
    a = _crypto_idea(name="Crypto High", content_hash="ca")
    a.fundability_score = 0.91
    a.generation_mode = "novel"
    b = _crypto_idea(
        name="Crypto Low",
        category=IdeaCategory.STABLECOIN_PAYMENTS,
        content_hash="cb",
    )
    b.fundability_score = 0.44
    b.generation_mode = "inversion"
    c = _crypto_idea(name="Crypto Unscored", content_hash="cc")  # no fundability_score
    await db.save_idea(a)
    await db.save_idea(b)
    await db.save_idea(c)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestCryptoRoutes:
    @pytest.mark.asyncio
    async def test_api_top_returns_only_scored_sorted(self, client):
        resp = await client.get("/api/crypto/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        names = [d["name"] for d in data]
        assert names == ["Crypto High", "Crypto Low"]  # sorted desc, unscored excluded
        assert data[0]["fundability_score"] == 0.91
        assert data[0]["category"] == "onchain-security"

    @pytest.mark.asyncio
    async def test_page_renders_scored_ideas(self, client):
        resp = await client.get("/crypto")
        assert resp.status_code == 200
        html = resp.text
        assert "Crypto High" in html
        assert "Crypto Unscored" not in html

    @pytest.mark.asyncio
    async def test_category_filter_narrows(self, client):
        resp = await client.get("/crypto?category=stablecoin-payments")
        html = resp.text
        assert "Crypto Low" in html
        assert "Crypto High" not in html

    @pytest.mark.asyncio
    async def test_churn_crypto_path(self, client, monkeypatch):
        """lab=crypto routes through generate_idea_llm + score_fundability + save."""
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult

        idea = _crypto_idea(
            name="Churned Crypto",
            tagline="usage-metered rpc gateway with multi-provider failover",
            description=(
                "A distinct web3 infra product: a usage-metered RPC gateway with "
                "per-method rate limits and multi-provider failover, billed per "
                "request with a paid team tier."
            ),
            category=IdeaCategory.WEB3_INFRA,
            content_hash="churn-crypto-1",
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
        resp = await client.post("/api/churn", json={"lab": "crypto"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"] is not None
        assert data["idea"]["name"] == "Churned Crypto"
        assert data["idea"]["category"] == "web3-infra"
        assert isinstance(data["idea"]["fundability_score"], float)
