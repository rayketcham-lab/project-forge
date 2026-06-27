"""Tests for the v0.16 Sniper board — grounded competitive-displacement.

Covers the full stack:
  - market_intel: pure parsers, cached/degrading fetch, prompt rendering
  - snipe scorer: the named-incumbent gate + signal heuristics, angle rotation
  - generate_snipe_llm: grounded generation with a stub backend + injected intel
  - DB round-trip of snipe_score + target_incumbent
  - routes: /sniper page, /api/sniper/top filtering, churn lab=snipe
  - models: SNIPER_CATEGORIES grouping + route tuple derivation
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import (
    SNIPER_CATEGORIES,
    Idea,
    IdeaCategory,
)
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "sniper.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# market_intel                                                                #
# --------------------------------------------------------------------------- #

_HN_PAYLOAD = {
    "hits": [
        {
            "objectID": "1",
            "title": "Show HN: Open-source Calendly alternative",
            "url": "https://example.com/a",
            "points": 313,
            "num_comments": 51,
            "created_at": "2024-01-01T00:00:00Z",
        },
        {
            "objectID": "2",
            "title": "Calendly raised prices again",
            "url": "https://example.com/b",
            "points": 120,
            "num_comments": 88,
            "created_at": "2024-02-01T00:00:00Z",
        },
    ]
}

_GH_PAYLOAD = {
    "items": [
        {
            "full_name": "calcom/cal.com",
            "html_url": "https://github.com/calcom/cal.com",
            "stargazers_count": 30000,
            "description": "Open-source Calendly alternative",
        },
        {
            "full_name": "x/y",
            "html_url": "https://github.com/x/y",
            "stargazers_count": 491,
            "description": "Another scheduler",
        },
    ]
}


class TestMarketIntelParsers:
    def test_parse_hn_sorts_by_points_desc(self):
        from project_forge.feeds.market_intel import parse_hn

        items = parse_hn(_HN_PAYLOAD)
        assert [i["points"] for i in items] == [313, 120]
        assert items[0]["title"].startswith("Show HN")
        assert items[0]["comments"] == 51

    def test_parse_github_sorts_by_stars_desc(self):
        from project_forge.feeds.market_intel import parse_github

        items = parse_github(_GH_PAYLOAD)
        assert [i["stars"] for i in items] == [30000, 491]
        assert items[0]["title"] == "calcom/cal.com"

    def test_parsers_tolerate_empty(self):
        from project_forge.feeds.market_intel import parse_github, parse_hn

        assert parse_hn({}) == []
        assert parse_github({}) == []

    def test_slug(self):
        from project_forge.feeds.market_intel import slug

        assert slug("HashiCorp Vault") == "hashicorp-vault"
        assert slug("Bill.com") == "bill-com"

    def test_pick_incumbent_in_registry(self):
        from project_forge.feeds.market_intel import INCUMBENT_SEEDS, pick_incumbent

        name = pick_incumbent(IdeaCategory.MICRO_SAAS)
        assert name in INCUMBENT_SEEDS[IdeaCategory.MICRO_SAAS]

    def test_pick_incumbent_none_for_unregistered(self):
        from project_forge.feeds.market_intel import pick_incumbent

        # SELF_IMPROVEMENT is not a sniper hunting ground.
        assert pick_incumbent(IdeaCategory.SELF_IMPROVEMENT) is None

    def test_every_sniper_category_has_incumbents(self):
        from project_forge.feeds.market_intel import INCUMBENT_SEEDS

        for cat in SNIPER_CATEGORIES:
            assert INCUMBENT_SEEDS.get(cat), f"no incumbents for {cat}"
            assert len(INCUMBENT_SEEDS[cat]) >= 5


class TestMarketIntelFetch:
    def _http_stub(self):
        def _get(url, *, timeout=12.0):
            if "hn.algolia.com" in url:
                return json.dumps(_HN_PAYLOAD).encode()
            if "api.github.com" in url:
                return json.dumps(_GH_PAYLOAD).encode()
            raise AssertionError(f"unexpected url {url}")

        return _get

    def test_build_bundle_merges_sources(self):
        from project_forge.feeds.market_intel import build_intel_bundle

        bundle = build_intel_bundle("Calendly", http_get=self._http_stub())
        assert bundle["incumbent"] == "Calendly"
        assert bundle["hn"] and bundle["oss_challengers"]
        assert bundle["oss_challengers"][0]["stars"] == 30000

    def test_fetch_degrades_to_empty_on_network_error(self):
        from project_forge.feeds.market_intel import fetch_incumbent_intel

        def _boom(url, *, timeout=12.0):
            raise OSError("network down")

        bundle = fetch_incumbent_intel("Calendly", http_get=_boom)
        assert bundle["hn"] == []
        assert bundle["oss_challengers"] == []

    def test_fetch_uses_cache_when_fresh(self, tmp_path):
        from datetime import timedelta

        from project_forge.feeds.cache import FeedCache
        from project_forge.feeds.market_intel import fetch_incumbent_intel

        cache = FeedCache(tmp_path / "calendly.json", ttl=timedelta(hours=24))
        calls = {"n": 0}

        def _counting(url, *, timeout=12.0):
            calls["n"] += 1
            return self._http_stub()(url, timeout=timeout)

        fetch_incumbent_intel("Calendly", cache=cache, http_get=_counting)
        first = calls["n"]
        assert first > 0
        # Second call should hit the cache, not the network.
        fetch_incumbent_intel("Calendly", cache=cache, http_get=_counting)
        assert calls["n"] == first

    def test_format_for_prompt_includes_signal_and_sources(self):
        from project_forge.feeds.market_intel import (
            build_intel_bundle,
            format_intel_for_prompt,
        )

        bundle = build_intel_bundle("Calendly", http_get=self._http_stub())
        text = format_intel_for_prompt(bundle)
        assert "Calendly" in text
        assert "313 pts" in text
        assert "30000★" in text
        assert "github.com/calcom/cal.com" in text

    def test_format_for_prompt_honest_when_empty(self):
        from project_forge.feeds.market_intel import format_intel_for_prompt

        text = format_intel_for_prompt({"incumbent": "X", "hn": [], "oss_challengers": []})
        assert "no live signal" in text.lower()


# --------------------------------------------------------------------------- #
# snipe scorer + angles                                                       #
# --------------------------------------------------------------------------- #


def _snipe_idea(**over) -> Idea:
    base = dict(
        name="Cheaper Scheduler",
        tagline="open-source calendly alternative at a flat price",
        description=(
            "Calendly is overpriced and enterprise-only; SMBs are locked out. "
            "We snipe with an open-source, self-hostable scheduler — AI-native "
            "smart slots — starting from the freelancer beachhead, because now "
            "the price hikes have pushed users to look for alternatives."
        ),
        category=IdeaCategory.MICRO_SAAS,
        market_analysis=(
            "Calendly is a category leader doing $100M+ ARR [approx] with "
            "millions of users; OSS challengers already pull 30000 stars."
        ),
        feasibility_score=0.7,
        mvp_scope="Phase 1 freelancer beachhead. Phase 2 teams. Phase 3 API.",
        tech_stack=["typescript", "next.js", "postgres"],
    )
    base.update(over)
    return Idea(**base)


class TestSnipeHeuristic:
    def test_named_incumbent_with_full_signal_scores_high(self):
        from project_forge.engine.snipe import score_snipe_heuristic

        idea = _snipe_idea(target_incumbent="Calendly")
        assert score_snipe_heuristic(idea) >= 0.75

    def test_missing_incumbent_is_penalized(self):
        from project_forge.engine.snipe import score_snipe_heuristic

        named = score_snipe_heuristic(_snipe_idea(target_incumbent="Calendly"))
        unnamed = score_snipe_heuristic(_snipe_idea(target_incumbent=None))
        assert unnamed < named

    def test_vague_idea_scores_low(self):
        from project_forge.engine.snipe import score_snipe_heuristic

        vague = Idea(
            name="A Tool",
            tagline="a tool for stuff",
            description="It does things for people.",
            category=IdeaCategory.MICRO_SAAS,
            market_analysis="People might pay.",
            feasibility_score=0.5,
            mvp_scope="build it",
            tech_stack=["python"],
        )
        assert score_snipe_heuristic(vague) < 0.35

    def test_score_in_range(self):
        from project_forge.engine.snipe import score_snipe_heuristic

        s = score_snipe_heuristic(_snipe_idea(target_incumbent="Calendly"))
        assert 0.0 <= s <= 1.0


class TestSnipeAngleRotation:
    @pytest.mark.asyncio
    async def test_pick_least_used_angle_prefers_unused(self, db):
        from project_forge.engine.snipe import SNIPE_ANGLES, pick_least_used_angle

        # Saturate the first angle with snipe ideas in this category.
        for i in range(3):
            idea = _snipe_idea(
                name=f"Snipe {i}",
                target_incumbent="Calendly",
                content_hash=f"h{i}",
            )
            idea.generation_mode = "snipe"
            idea.artifact_type = SNIPE_ANGLES[0]
            await db.save_idea(idea)
        chosen = await pick_least_used_angle(db, IdeaCategory.MICRO_SAAS)
        assert chosen != SNIPE_ANGLES[0]

    @pytest.mark.asyncio
    async def test_score_snipe_out_of_band_uses_heuristic(self, db, monkeypatch):
        import project_forge.engine.snipe as snipe_mod

        # Force no backend so the borderline band can't call the LLM.
        monkeypatch.setattr(snipe_mod, "resolve_cheap_backend", lambda: None)
        idea = _snipe_idea(target_incumbent="Calendly")
        s = await snipe_mod.score_snipe(idea)
        assert 0.0 <= s <= 1.0


# --------------------------------------------------------------------------- #
# generate_snipe_llm                                                          #
# --------------------------------------------------------------------------- #

_SNIPE_PAYLOAD = {
    "target_incumbent": "Calendly",
    "name": "FlatBook",
    "tagline": "self-hosted scheduling at a flat monthly price",
    "description": (
        "Calendly's per-seat pricing punishes growing teams. FlatBook is the "
        "open-source, self-hostable alternative with one flat price, starting "
        "from the indie-team beachhead, because the latest price hike pushed "
        "teams to shop around."
    ),
    "market_analysis": "Calendly is a category leader; OSS challengers pull 30k stars [approx].",
    "mvp_scope": "Phase 1 indie teams. Phase 2 SSO. Phase 3 marketplace.",
    "tech_stack": ["typescript", "next.js", "postgres"],
    "feasibility_score": 0.78,
}


def _stub_backend(payload: dict, name: str = "stub:haiku") -> MagicMock:
    backend = MagicMock()
    backend.name = name
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


class TestGenerateSnipe:
    @pytest.mark.asyncio
    async def test_generates_with_target_incumbent_and_angle(self, db):
        from project_forge.engine.llm_generator import generate_snipe_llm
        from project_forge.engine.snipe import SNIPE_ANGLES

        intel = {"incumbent": "Calendly", "hn": [], "oss_challengers": []}
        result = await generate_snipe_llm(
            db,
            IdeaCategory.MICRO_SAAS,
            incumbent="Calendly",
            intel=intel,
            backend=_stub_backend(_SNIPE_PAYLOAD),
        )
        assert result is not None
        assert result.idea.target_incumbent == "Calendly"
        assert result.idea.generation_mode == "snipe"
        assert result.idea.artifact_type in SNIPE_ANGLES
        assert result.mode == "snipe"

    @pytest.mark.asyncio
    async def test_falls_back_to_chosen_incumbent_when_payload_omits_it(self, db):
        from project_forge.engine.llm_generator import generate_snipe_llm

        payload = dict(_SNIPE_PAYLOAD)
        payload.pop("target_incumbent")
        intel = {"incumbent": "Okta", "hn": [], "oss_challengers": []}
        result = await generate_snipe_llm(
            db,
            IdeaCategory.SECURITY_TOOL,
            incumbent="Okta",
            intel=intel,
            backend=_stub_backend(payload),
        )
        assert result is not None
        assert result.idea.target_incumbent == "Okta"

    @pytest.mark.asyncio
    async def test_none_when_no_backend(self, db, monkeypatch):
        import project_forge.engine.llm_generator as gen

        monkeypatch.setattr(gen, "resolve_cheap_backend", lambda: None)
        result = await gen.generate_snipe_llm(
            db,
            IdeaCategory.MICRO_SAAS,
            incumbent="Calendly",
            intel={"incumbent": "Calendly", "hn": [], "oss_challengers": []},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_none_on_unparseable(self, db):
        from project_forge.engine.llm_generator import generate_snipe_llm

        bad = MagicMock()
        bad.name = "stub"
        bad.call = MagicMock(return_value="not json at all")
        result = await generate_snipe_llm(
            db,
            IdeaCategory.MICRO_SAAS,
            incumbent="Calendly",
            intel={"incumbent": "Calendly", "hn": [], "oss_challengers": []},
            backend=bad,
        )
        assert result is None


# --------------------------------------------------------------------------- #
# DB round-trip                                                               #
# --------------------------------------------------------------------------- #


class TestDbRoundTrip:
    @pytest.mark.asyncio
    async def test_snipe_fields_persist(self, db):
        idea = _snipe_idea(target_incumbent="Datadog")
        idea.snipe_score = 0.81
        idea.generation_mode = "snipe"
        idea.artifact_type = "unbundle"
        await db.save_idea(idea)
        got = await db.get_idea(idea.id)
        assert got is not None
        assert got.snipe_score == 0.81
        assert got.target_incumbent == "Datadog"
        assert got.artifact_type == "unbundle"


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "test_sniper_routes.db"
    await db.connect()
    # Two snipe-scored ideas + one non-snipe idea that must NOT surface.
    a = _snipe_idea(name="Snipe High", target_incumbent="Calendly", content_hash="ha")
    a.snipe_score = 0.92
    a.generation_mode = "snipe"
    a.artifact_type = "open-source"
    b = _snipe_idea(name="Snipe Low", target_incumbent="Okta", category=IdeaCategory.SECURITY_TOOL, content_hash="hb")
    b.snipe_score = 0.40
    b.generation_mode = "snipe"
    b.artifact_type = "down-market"
    c = _snipe_idea(name="Plain Idea", content_hash="hc")  # no snipe_score
    await db.save_idea(a)
    await db.save_idea(b)
    await db.save_idea(c)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestSniperRoutes:
    @pytest.mark.asyncio
    async def test_api_top_returns_only_snipe_scored_sorted(self, client):
        resp = await client.get("/api/sniper/top?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        names = [d["name"] for d in data]
        assert names == ["Snipe High", "Snipe Low"]  # sorted desc, plain excluded
        assert data[0]["target_incumbent"] == "Calendly"
        assert data[0]["angle"] == "open-source"

    @pytest.mark.asyncio
    async def test_page_renders_with_incumbent_badge(self, client):
        resp = await client.get("/sniper")
        assert resp.status_code == 200
        html = resp.text
        assert "Snipe High" in html
        assert "vs." in html and "Calendly" in html
        assert "Plain Idea" not in html

    @pytest.mark.asyncio
    async def test_category_filter_narrows(self, client):
        resp = await client.get("/sniper?category=security-tool")
        html = resp.text
        assert "Snipe Low" in html
        assert "Snipe High" not in html

    @pytest.mark.asyncio
    async def test_churn_snipe_path(self, client, monkeypatch):
        """lab=snipe routes through generate_snipe_llm + score_snipe + save."""
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult

        idea = _snipe_idea(
            name="Churned Snipe",
            target_incumbent="Vault",
            category=IdeaCategory.CRYPTO_INFRASTRUCTURE,
            content_hash="churn1",
        )
        idea.generation_mode = "snipe"
        idea.artifact_type = "compliance-shift"

        async def _fake_generate(db_, category, **kw):
            return LLMGenerationResult(
                idea=idea,
                mode="snipe",
                persona="p",
                backend="stub",
                raw_response="{}",
                artifact_type="compliance-shift",
            )

        monkeypatch.setattr(gen, "generate_snipe_llm", _fake_generate)
        resp = await client.post("/api/churn", json={"lab": "snipe"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"] is not None
        assert data["idea"]["target_incumbent"] == "Vault"
        assert isinstance(data["idea"]["snipe_score"], float)


# --------------------------------------------------------------------------- #
# models grouping                                                             #
# --------------------------------------------------------------------------- #


class TestSniperGrouping:
    def test_grouping_spans_commercial_and_security(self):
        assert IdeaCategory.MICRO_SAAS in SNIPER_CATEGORIES
        assert IdeaCategory.CRYPTO_INFRASTRUCTURE in SNIPER_CATEGORIES
        assert IdeaCategory.SECURITY_TOOL in SNIPER_CATEGORIES

    def test_route_tuple_matches_canonical(self):
        from project_forge.web import routes

        assert set(routes._SNIPER_CATEGORIES) == {c.value for c in SNIPER_CATEGORIES}
