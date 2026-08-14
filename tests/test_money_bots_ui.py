"""Tests for the /money-bots board + /api/money-bots/top endpoint.

v0.24 rework. The board used to be /explore filtered to eight product
categories and ranked by fundability, which is why it produced SaaS
pitches instead of bots. It now lists CAPITAL-DEPLOYMENT STRATEGIES across
the five bot categories, ranked by bot_edge_score, and every card carries
the strategy itself: venue, the API primitives the bot calls, the
mechanism the yield comes from, capital band, how the edge decays, and
when to switch it off.

Two things this pins deliberately:
  * an idea with no bot_edge_score never reaches the board (the gate), and
  * a card renders the BotSpec, not a paragraph of prose — if the spec
    stops being surfaced, the board is back to being explore with a filter.
"""

from __future__ import annotations

import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import BotSpec, BotVenueFamily, Idea, IdeaCategory
from project_forge.web.app import app, db


def _spec(venue: str, family: BotVenueFamily, floor: float = 500.0) -> BotSpec:
    return BotSpec(
        venue=venue,
        venue_url=f"https://docs.example.com/{venue.lower()}/rewards",
        family=family,
        api_primitives=["REST order placement", "websocket book feed"],
        mechanism=f"{venue} pays a published liquidity budget for resting two-sided size.",
        capital_floor_usd=floor,
        capital_target_usd=floor * 20,
        expected_return="Share of the reward pool, diluted pro-rata",
        edge_decay="Reward pool is fixed — yield falls as competing makers arrive",
        kill_criteria=["reward per minute drops below fees plus adverse selection"],
        validation_plan=["one book, floor capital, 14 days, measure realised share"],
        legality_note="Published venue program with public rules",
        human_touchpoints="Weekly book selection review",
    )


def _bot_idea(
    name: str,
    cat: IdeaCategory,
    score: float | None,
    spec: BotSpec | None = None,
) -> Idea:
    idea = Idea(
        name=name,
        tagline=f"tag {name}",
        description="d" * 80,
        category=cat,
        market_analysis="m" * 40,
        feasibility_score=0.7,
        mvp_scope="mvp" * 5,
        tech_stack=["python", "websockets"],
    )
    idea.bot_edge_score = score
    idea.bot_spec = spec
    return idea


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_money_bots.db"
    await db.connect()
    # 3 scored strategies, 1 unscored (gated out), 1 outside the board.
    await db.save_idea(
        _bot_idea(
            "Reward Minute Maker",
            IdeaCategory.INCENTIVE_CAPTURE,
            0.90,
            _spec("Polymarket", BotVenueFamily.PREDICTION_MARKETS, 500.0),
        )
    )
    await db.save_idea(
        _bot_idea(
            "Funding Carry Holder",
            IdeaCategory.BASIS_CARRY,
            0.65,
            _spec("Hyperliquid", BotVenueFamily.CRYPTO_DEFI, 2000.0),
        )
    )
    await db.save_idea(
        _bot_idea(
            "Thin Book Quoter",
            IdeaCategory.MARKET_MAKING,
            0.40,
            _spec("ProphetX", BotVenueFamily.SPORTSBOOK, 1000.0),
        )
    )
    await db.save_idea(_bot_idea("Unscored Draft", IdeaCategory.CROSS_VENUE_ARBITRAGE, None))
    await db.save_idea(_bot_idea("Sec Tool", IdeaCategory.SECURITY_TOOL, 0.99))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


class TestApiTop:
    @pytest.mark.asyncio
    async def test_returns_only_scored_bot_categories(self, client):
        resp = await client.get("/api/money-bots/top?limit=10")
        assert resp.status_code == 200
        names = [d["name"] for d in resp.json()]
        assert set(names) == {"Reward Minute Maker", "Funding Carry Holder", "Thin Book Quoter"}

    @pytest.mark.asyncio
    async def test_sorted_by_bot_edge_desc(self, client):
        resp = await client.get("/api/money-bots/top?limit=10")
        scores = [d["bot_edge_score"] for d in resp.json()]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_limit_param_caps_results(self, client):
        resp = await client.get("/api/money-bots/top?limit=2")
        assert len(resp.json()) == 2

    @pytest.mark.asyncio
    async def test_payload_carries_the_strategy(self, client):
        """The API is only useful if it returns the edge, not just a title."""
        resp = await client.get("/api/money-bots/top?limit=1")
        item = resp.json()[0]
        for field in (
            "id",
            "name",
            "tagline",
            "category",
            "bot_edge_score",
            "venue",
            "family",
            "mechanism",
            "capital_floor_usd",
            "status",
        ):
            assert field in item
        assert item["venue"] == "Polymarket"
        assert item["capital_floor_usd"] == 500.0


class TestHtmlPage:
    @pytest.mark.asyncio
    async def test_renders_scored_strategies(self, client):
        resp = await client.get("/money-bots")
        assert resp.status_code == 200
        html = resp.text
        assert "Reward Minute Maker" in html
        assert "Funding Carry Holder" in html
        # Gated out: no score, and outside the board entirely.
        assert "Unscored Draft" not in html
        assert "Sec Tool" not in html

    @pytest.mark.asyncio
    async def test_card_shows_the_spec_not_just_prose(self, client):
        html = (await client.get("/money-bots")).text
        assert "Polymarket" in html
        assert "Hyperliquid" in html
        # Capital band, the mechanism, and the stop condition all surface.
        assert "500" in html
        assert "published liquidity budget" in html
        assert "adverse selection" in html

    @pytest.mark.asyncio
    async def test_api_primitives_are_listed(self, client):
        html = (await client.get("/money-bots")).text
        assert "websocket book feed" in html

    @pytest.mark.asyncio
    async def test_category_filter_narrows_results(self, client):
        resp = await client.get("/money-bots?category=incentive-capture")
        html = resp.text
        assert "Reward Minute Maker" in html
        assert "Funding Carry Holder" not in html

    @pytest.mark.asyncio
    async def test_unknown_category_falls_back_to_all(self, client):
        resp = await client.get("/money-bots?category=not-real")
        assert resp.status_code == 200
        assert "Reward Minute Maker" in resp.text

    @pytest.mark.asyncio
    async def test_playbook_of_known_strategies_is_linked(self, client):
        """The board carries the library of edges that already work."""
        html = (await client.get("/money-bots")).text
        assert "playbook" in html.lower()

    @pytest.mark.asyncio
    async def test_mechanism_corpus_is_always_available(self, client):
        """The board must always be able to answer "how does a bot make money",
        with or without anything generated — as a collapsed reference block at
        the bottom, below the generated strategies."""
        html = (await client.get("/money-bots?category=basis-carry")).text

        assert "Perpetual funding carry" in html
        assert "How bots actually make money" in html
        # It lives below the strategies, and it is collapsed (no `open`).
        assert html.index("Generated strategies") < html.index("How bots actually make money")
        block = html[html.index('<details class="ref-block" id="playbook"') :][:80]
        assert "open" not in block

    @pytest.mark.asyncio
    async def test_flagged_strategies_are_shown_with_their_reason(self, client):
        """A red-teamed strategy stays visible — the reason is the value."""
        killed = _bot_idea(
            "Doomed Quoter",
            IdeaCategory.MARKET_MAKING,
            0.62,
            _spec("Polymarket", BotVenueFamily.PREDICTION_MARKETS),
        )
        killed.bot_spec.panel_verdict = "flagged"
        killed.bot_spec.surviving_objection = "the reward is smaller than the minimum tick"
        await db.save_idea(killed)

        html = (await client.get("/money-bots")).text
        assert "Doomed Quoter" in html
        assert "smaller than the minimum tick" in html
        assert "Flagged" in html


class TestChurn:
    """Churn Now on this board must run the GROUNDED bot pipeline.

    The generic churn path produces an idea with no BotSpec, which the gate
    rejects and the board never shows — a button that silently does nothing
    useful. Here it runs one probe cycle and reports what happened.
    """

    @pytest.mark.asyncio
    async def test_churn_runs_a_probe_cycle_and_reports_a_quiet_one(self, client, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.setattr(ls, "_bot_fetch_programs", lambda: [])
        resp = await client.post("/api/churn", json={"lab": "money"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"] is None
        assert "no new venue program" in (data["message"] or "")

        # The attempt is still on the record.
        assert (await db.list_bot_probes(limit=1))[0]["admitted"] is False

    @pytest.mark.asyncio
    async def test_churn_returns_the_strategy_when_one_is_admitted(self, client, monkeypatch):
        from project_forge.engine.bot_depth import StressResult
        from project_forge.web import lifespan_scheduler as ls

        program = {
            "venue": "Hyperliquid",
            "family": BotVenueFamily.CRYPTO_DEFI.value,
            "category": IdeaCategory.BASIS_CARRY.value,
            "title": "funding rate endpoint added",
            "url": "https://github.com/example/sdk/releases/tag/v1",
            "summary": "funding history endpoint",
            "source": "github-release",
            "program_score": 5,
        }
        fresh = _bot_idea(
            "Churned Carry Bot",
            IdeaCategory.BASIS_CARRY,
            None,
            _spec("Hyperliquid", BotVenueFamily.CRYPTO_DEFI, 2000.0),
        )
        fresh.generation_mode = "bot"

        class _Result:
            idea = fresh
            mode = "bot"
            persona = "p"
            backend = "fake"
            raw_response = "{}"
            artifact_type = None

        async def _gen(*_a, **_k):
            return _Result()

        async def _survive(idea):
            return StressResult(idea=idea, survived=True, passes=4)

        async def _score(_i):
            return 0.77

        monkeypatch.setattr(ls, "_bot_fetch_programs", lambda: [program])
        monkeypatch.setattr(ls, "_bot_generate", _gen)
        monkeypatch.setattr(ls, "_bot_stress", _survive)
        monkeypatch.setattr(ls, "_bot_score", _score)

        data = (await client.post("/api/churn", json={"lab": "money"})).json()
        assert data["idea"] is not None
        assert data["idea"]["name"] == "Churned Carry Bot"
        assert data["idea"]["venue"] == "Hyperliquid"
        assert data["idea"]["bot_edge_score"] == 0.77


class TestScaffoldEndpoint:
    @pytest.mark.asyncio
    async def test_scaffolds_a_runnable_repo(self, client, tmp_path, monkeypatch):
        from project_forge.web import routes

        monkeypatch.setattr(routes, "_bot_scaffold_root", lambda: tmp_path)
        top = (await client.get("/api/money-bots/top?limit=1")).json()[0]

        resp = await client.post(f"/api/scaffold-bot/{top['id']}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scaffolded"] is True
        assert "README.md" in data["files"]
        assert (tmp_path / data["repo_name"] / "src").is_dir()

    @pytest.mark.asyncio
    async def test_refuses_an_idea_with_no_spec(self, client, tmp_path, monkeypatch):
        from project_forge.web import routes

        monkeypatch.setattr(routes, "_bot_scaffold_root", lambda: tmp_path)
        spec_less = _bot_idea("No Spec Here", IdeaCategory.MARKET_MAKING, 0.8)
        await db.save_idea(spec_less)

        resp = await client.post(f"/api/scaffold-bot/{spec_less.id}")
        assert resp.status_code == 400
        assert "spec" in resp.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_unknown_idea_is_404(self, client):
        assert (await client.post("/api/scaffold-bot/deadbeef")).status_code == 404


class TestDashboardStats:
    @pytest.mark.asyncio
    async def test_money_bot_count_in_stats(self, client):
        resp = await client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "money_bot_count" in data
        assert "auto_promoted_count" in data
        # 4 ideas across the bot categories (3 scored + 1 unscored draft).
        assert data["money_bot_count"] == 4


class TestCardIsCompact:
    """Cards were dumping the entire spec inline — 600 chars of return text,
    600 of objection, and up to ten kill criteria per card. Four of those is
    a wall of text, not a board.

    The card now shows what you scan by (venue, edge, capital, one-line
    mechanism) and folds the rest behind one toggle.
    """

    @pytest.mark.asyncio
    async def test_long_fields_are_behind_a_toggle(self, client):
        html = (await client.get("/money-bots")).text
        assert 'class="card-more"' in html

        card_start = html.index('class="strategy-card')
        card = html[card_start : card_start + 40000]
        more_at = card.index('class="card-more"')

        # The scannable half comes first...
        assert card.index("strategy-name") < more_at
        assert card.index("capital-chip") < more_at
        # ...and the long-form spec is inside the toggle.
        assert card.index("Switches off when") > more_at

    @pytest.mark.asyncio
    async def test_mechanism_is_clamped_not_dumped(self, client):
        html = (await client.get("/money-bots")).text
        assert 'class="card-mech"' in html

    @pytest.mark.asyncio
    async def test_the_old_inline_dump_is_gone(self, client):
        html = (await client.get("/money-bots")).text
        assert 'class="strategy-facts"' not in html


class TestFlaggedAreCountedNotDisplayed:
    """Flagged strategies are a learning signal, not board content.

    The board shows what survived the red team. What did not survived is
    counted and listed compactly — you can see it and audit it, but it does
    not compete for attention with the strategies that actually passed.
    """

    @pytest.mark.asyncio
    async def test_flagged_are_not_rendered_as_cards(self, client):
        killed = _bot_idea(
            "Doomed Quoter Two",
            IdeaCategory.MARKET_MAKING,
            0.62,
            _spec("Polymarket", BotVenueFamily.PREDICTION_MARKETS),
        )
        killed.bot_spec.panel_verdict = "flagged"
        killed.bot_spec.surviving_objection = "the reward is smaller than the minimum tick"
        await db.save_idea(killed)

        html = (await client.get("/money-bots")).text
        cards = re.findall(r'<article class="strategy-card.*?</article>', html, re.S)
        assert not any("Doomed Quoter Two" in c for c in cards), "a flagged strategy must not render as a card"
        # But it is visible and counted somewhere on the page.
        assert "Doomed Quoter Two" in html
        assert "flagged" in html.lower()

    @pytest.mark.asyncio
    async def test_flagged_block_is_collapsed(self, client):
        killed = _bot_idea(
            "Collapsed Check",
            IdeaCategory.BASIS_CARRY,
            0.5,
            _spec("Hyperliquid", BotVenueFamily.CRYPTO_DEFI),
        )
        killed.bot_spec.panel_verdict = "flagged"
        await db.save_idea(killed)

        html = (await client.get("/money-bots")).text
        block = html[html.index('id="flagged-log"') :][:120]
        assert "open" not in block

    @pytest.mark.asyncio
    async def test_counts_are_shown(self, client):
        html = (await client.get("/money-bots")).text
        assert "vetted" in html.lower()
        assert "flagged" in html.lower()
