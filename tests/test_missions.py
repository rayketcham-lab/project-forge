"""Tests for v0.18 Missions — operator-directed generation (issue #84).

A Mission is the operator pointing the think tank at a target: a free-text
brief plus up to 3 grounding URLs. Ideas generated against it ride the
existing `seed` anchor in `generate_idea_llm` and are stamped
`generation_mode='mission'` + `mission_id`.

Covers the full stack:
  - models: Mission / MissionCreateRequest validation
  - storage: missions CRUD, ideas.mission_id round-trip, round-robin picker
  - engine: build_mission_seed grounding + generate_mission_idea stamping
  - scheduler: mission watermark + cadence registration + _fire_mission
  - routes: /missions page, /api/missions CRUD, generate, status, rate limit
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from project_forge.models import (
    Idea,
    IdeaCategory,
    Mission,
    MissionCreateRequest,
)


def _mk_idea(**kw) -> Idea:
    base = dict(
        name="Mission Idea",
        tagline="a mission-anchored idea",
        description="Solves the operator's stated problem.",
        category=IdeaCategory.MICRO_SAAS,
        market_analysis="Operators with this exact pain.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: MVP",
        tech_stack=["python"],
    )
    base.update(kw)
    return Idea(**base)


def _mk_mission(**kw) -> Mission:
    base = dict(
        title="PKI blind spots",
        brief="Find product ideas around certificate lifecycle pain in mid-size enterprises.",
    )
    base.update(kw)
    return Mission(**base)


# --------------------------------------------------------------------------- #
# models                                                                      #
# --------------------------------------------------------------------------- #


class TestMissionModel:
    def test_defaults(self):
        m = _mk_mission()
        assert m.status == "active"
        assert m.urls == []
        assert m.category is None
        assert m.last_generated_at is None
        assert len(m.id) == 12

    def test_brief_too_short_rejected(self):
        with pytest.raises(ValidationError):
            _mk_mission(brief="too short")

    def test_brief_too_long_rejected(self):
        with pytest.raises(ValidationError):
            _mk_mission(brief="x" * 4001)

    def test_brief_whitespace_only_rejected(self):
        with pytest.raises(ValidationError):
            _mk_mission(brief="   \n\t   ")

    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError):
            _mk_mission(title="")

    def test_more_than_three_urls_rejected(self):
        urls = [f"https://example.com/{i}" for i in range(4)]
        with pytest.raises(ValidationError):
            _mk_mission(urls=urls)

    def test_non_http_url_rejected(self):
        with pytest.raises(ValidationError):
            _mk_mission(urls=["javascript:alert(1)"])
        with pytest.raises(ValidationError):
            _mk_mission(urls=["ftp://example.com/x"])

    def test_valid_urls_accepted(self):
        m = _mk_mission(urls=["https://example.com/a", "http://example.com/b"])
        assert len(m.urls) == 2

    def test_category_typed(self):
        m = _mk_mission(category=IdeaCategory.AGENT_SECURITY)
        assert m.category is IdeaCategory.AGENT_SECURITY


class TestMissionCreateRequest:
    def test_valid_category_string(self):
        req = MissionCreateRequest(
            title="t" * 3,
            brief="a perfectly reasonable brief here",
            category="micro-saas",
        )
        assert req.category == "micro-saas"

    def test_bogus_category_rejected(self):
        with pytest.raises(ValidationError):
            MissionCreateRequest(
                title="ttt",
                brief="a perfectly reasonable brief here",
                category="not-a-category",
            )

    def test_none_category_ok(self):
        req = MissionCreateRequest(title="ttt", brief="a perfectly reasonable brief here")
        assert req.category is None


# --------------------------------------------------------------------------- #
# storage                                                                     #
# --------------------------------------------------------------------------- #


class TestMissionStorage:
    @pytest.mark.asyncio
    async def test_save_get_roundtrip(self, db):
        m = _mk_mission(urls=["https://example.com/doc"], category=IdeaCategory.MICRO_SAAS)
        await db.save_mission(m)
        got = await db.get_mission(m.id)
        assert got is not None
        assert got.title == m.title
        assert got.brief == m.brief
        assert got.urls == ["https://example.com/doc"]
        assert got.category is IdeaCategory.MICRO_SAAS
        assert got.status == "active"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, db):
        assert await db.get_mission("nope") is None

    @pytest.mark.asyncio
    async def test_list_missions_filters_by_status(self, db):
        a = _mk_mission(title="active one")
        b = _mk_mission(title="paused one")
        await db.save_mission(a)
        await db.save_mission(b)
        await db.update_mission_status(b.id, "paused")
        active = await db.list_missions(status="active")
        assert [m.id for m in active] == [a.id]
        everything = await db.list_missions()
        assert {m.id for m in everything} == {a.id, b.id}

    @pytest.mark.asyncio
    async def test_touch_sets_last_generated_at(self, db):
        m = _mk_mission()
        await db.save_mission(m)
        await db.touch_mission_generated(m.id)
        got = await db.get_mission(m.id)
        assert got.last_generated_at is not None

    @pytest.mark.asyncio
    async def test_pick_next_prefers_never_generated_then_oldest(self, db):
        stale = _mk_mission(title="stale")
        stale.last_generated_at = datetime.now(UTC) - timedelta(hours=9)
        fresh = _mk_mission(title="fresh")
        fresh.last_generated_at = datetime.now(UTC)
        never = _mk_mission(title="never")
        for m in (stale, fresh, never):
            await db.save_mission(m)
        picked = await db.pick_next_mission()
        assert picked.id == never.id
        # once 'never' has been generated against, the stalest wins
        await db.touch_mission_generated(never.id)
        picked = await db.pick_next_mission()
        assert picked.id == stale.id

    @pytest.mark.asyncio
    async def test_pick_next_skips_paused_and_archived(self, db):
        a = _mk_mission(title="paused")
        b = _mk_mission(title="archived")
        await db.save_mission(a)
        await db.save_mission(b)
        await db.update_mission_status(a.id, "paused")
        await db.update_mission_status(b.id, "archived")
        assert await db.pick_next_mission() is None

    @pytest.mark.asyncio
    async def test_idea_mission_id_roundtrip(self, db):
        m = _mk_mission()
        await db.save_mission(m)
        idea = _mk_idea(mission_id=m.id, content_hash="mi1")
        await db.save_idea(idea)
        got = await db.get_idea(idea.id)
        assert got.mission_id == m.id

    @pytest.mark.asyncio
    async def test_count_ideas_by_mission(self, db):
        m1 = _mk_mission(title="m one")
        m2 = _mk_mission(title="m two")
        await db.save_mission(m1)
        await db.save_mission(m2)
        await db.save_idea(_mk_idea(name="A", mission_id=m1.id, content_hash="c1"))
        await db.save_idea(_mk_idea(name="B", mission_id=m1.id, content_hash="c2"))
        await db.save_idea(_mk_idea(name="C", mission_id=m2.id, content_hash="c3"))
        await db.save_idea(_mk_idea(name="D", content_hash="c4"))  # unlinked
        counts = await db.count_ideas_by_mission()
        assert counts[m1.id] == 2
        assert counts[m2.id] == 1

    @pytest.mark.asyncio
    async def test_list_mission_ideas(self, db):
        m1 = _mk_mission(title="m one")
        m2 = _mk_mission(title="m two")
        await db.save_mission(m1)
        await db.save_mission(m2)
        await db.save_idea(_mk_idea(name="A", mission_id=m1.id, content_hash="c1"))
        await db.save_idea(_mk_idea(name="C", mission_id=m2.id, content_hash="c3"))
        await db.save_idea(_mk_idea(name="D", content_hash="c4"))
        only_m1 = await db.list_mission_ideas(mission_id=m1.id)
        assert [i.name for i in only_m1] == ["A"]
        all_linked = await db.list_mission_ideas()
        assert {i.name for i in all_linked} == {"A", "C"}


# --------------------------------------------------------------------------- #
# engine                                                                      #
# --------------------------------------------------------------------------- #


def _fake_fetcher(title: str = "Fetched Doc", text: str = "fetched body text"):
    from project_forge.engine.url_ingest import UrlContent

    async def fetch(url: str) -> UrlContent:
        return UrlContent(url=url, domain="example.com", title=title, text=text)

    return fetch


class TestBuildMissionSeed:
    @pytest.mark.asyncio
    async def test_seed_contains_brief(self):
        from project_forge.engine.mission import build_mission_seed

        m = _mk_mission()
        seed = await build_mission_seed(m)
        assert m.brief in seed
        assert m.title in seed

    @pytest.mark.asyncio
    async def test_seed_includes_fetched_url_content(self):
        from project_forge.engine.mission import build_mission_seed

        m = _mk_mission(urls=["https://example.com/doc"])
        seed = await build_mission_seed(m, fetcher=_fake_fetcher(title="ACME RFC", text="rotation pain"))
        assert "ACME RFC" in seed
        assert "rotation pain" in seed

    @pytest.mark.asyncio
    async def test_fetch_failure_degrades_to_brief_only(self):
        from project_forge.engine.mission import build_mission_seed

        async def boom(url: str):
            raise RuntimeError("network down")

        m = _mk_mission(urls=["https://example.com/doc"])
        seed = await build_mission_seed(m, fetcher=boom)
        assert m.brief in seed

    @pytest.mark.asyncio
    async def test_seed_is_capped(self):
        from project_forge.engine.mission import MAX_SEED_CHARS, build_mission_seed

        m = _mk_mission(urls=["https://example.com/doc"])
        seed = await build_mission_seed(m, fetcher=_fake_fetcher(text="x" * 50000))
        assert len(seed) <= MAX_SEED_CHARS


def _patch_scoring(monkeypatch):
    """Pin fundability scoring so engine tests never resolve a real LLM
    backend (the `claude` CLI is on PATH in this environment)."""
    import project_forge.engine.fundability as fund_mod

    async def _fixed(idea):
        return 0.5

    monkeypatch.setattr(fund_mod, "score_fundability", _fixed)


class TestGenerateMissionIdea:
    @pytest.mark.asyncio
    async def test_stamps_mode_and_mission_id_and_persists(self, db, monkeypatch):
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult
        from project_forge.engine.mission import generate_mission_idea

        _patch_scoring(monkeypatch)
        m = _mk_mission()
        await db.save_mission(m)
        captured: dict = {}

        async def _fake_generate(db_, category, **kw):
            captured["category"] = category
            captured["seed"] = kw.get("seed")
            return LLMGenerationResult(
                idea=_mk_idea(content_hash="gen1"),
                mode="novel",
                persona="p",
                backend="stub",
                raw_response="{}",
            )

        monkeypatch.setattr(gen, "generate_idea_llm", _fake_generate)
        result = await generate_mission_idea(db, m)
        assert result is not None
        assert result.saved is True
        assert result.idea.generation_mode == "mission"
        assert result.idea.mission_id == m.id
        assert m.brief in captured["seed"]
        stored = await db.get_idea(result.idea.id)
        assert stored is not None
        assert stored.mission_id == m.id
        # watermark advanced so the cadence doesn't hammer
        got = await db.get_mission(m.id)
        assert got.last_generated_at is not None

    @pytest.mark.asyncio
    async def test_mission_category_respected(self, db, monkeypatch):
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult
        from project_forge.engine.mission import generate_mission_idea

        _patch_scoring(monkeypatch)
        m = _mk_mission(category=IdeaCategory.AGENT_SECURITY)
        await db.save_mission(m)
        captured: dict = {}

        async def _fake_generate(db_, category, **kw):
            captured["category"] = category
            return LLMGenerationResult(
                idea=_mk_idea(category=IdeaCategory.AGENT_SECURITY, content_hash="gen2"),
                mode="novel",
                persona="p",
                backend="stub",
                raw_response="{}",
            )

        monkeypatch.setattr(gen, "generate_idea_llm", _fake_generate)
        await generate_mission_idea(db, m)
        assert captured["category"] is IdeaCategory.AGENT_SECURITY

    @pytest.mark.asyncio
    async def test_no_backend_returns_none_without_touch(self, db, monkeypatch):
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.mission import generate_mission_idea

        m = _mk_mission()
        await db.save_mission(m)

        async def _no_idea(db_, category, **kw):
            return None

        monkeypatch.setattr(gen, "generate_idea_llm", _no_idea)
        assert await generate_mission_idea(db, m) is None
        got = await db.get_mission(m.id)
        assert got.last_generated_at is None  # cadence may retry later

    @pytest.mark.asyncio
    async def test_dedup_reject_still_touches_watermark(self, db, monkeypatch):
        import project_forge.engine.dedup as dedup_mod
        import project_forge.engine.llm_generator as gen
        from project_forge.engine.llm_generator import LLMGenerationResult
        from project_forge.engine.mission import generate_mission_idea

        _patch_scoring(monkeypatch)
        m = _mk_mission()
        await db.save_mission(m)

        async def _fake_generate(db_, category, **kw):
            return LLMGenerationResult(
                idea=_mk_idea(content_hash="dup1"),
                mode="novel",
                persona="p",
                backend="stub",
                raw_response="{}",
            )

        async def _reject(idea, db_):
            return idea, False, "near-duplicate"

        monkeypatch.setattr(gen, "generate_idea_llm", _fake_generate)
        monkeypatch.setattr(dedup_mod, "filter_and_save", _reject)
        result = await generate_mission_idea(db, m)
        assert result.saved is False
        assert result.reason == "near-duplicate"
        got = await db.get_mission(m.id)
        assert got.last_generated_at is not None


# --------------------------------------------------------------------------- #
# scheduler                                                                   #
# --------------------------------------------------------------------------- #


class TestMissionCadence:
    @pytest.mark.asyncio
    async def test_watermark_skips_when_no_active_missions(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_mission

        delay = await seconds_until_next_mission(db, timedelta(hours=4))
        assert delay > 0

    @pytest.mark.asyncio
    async def test_watermark_fires_for_never_generated_mission(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_mission

        await db.save_mission(_mk_mission())
        delay = await seconds_until_next_mission(db, timedelta(hours=4))
        assert delay == 0.0

    @pytest.mark.asyncio
    async def test_watermark_waits_after_recent_generation(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_mission

        m = _mk_mission()
        await db.save_mission(m)
        await db.touch_mission_generated(m.id)
        delay = await seconds_until_next_mission(db, timedelta(hours=4))
        assert delay > 0

    @pytest.mark.asyncio
    async def test_paused_mission_does_not_fire(self, db):
        from project_forge.web.lifespan_scheduler import seconds_until_next_mission

        m = _mk_mission()
        await db.save_mission(m)
        await db.update_mission_status(m.id, "paused")
        delay = await seconds_until_next_mission(db, timedelta(hours=4))
        assert delay > 0

    def test_cadence_registered(self):
        from project_forge.web.lifespan_scheduler import (
            _fire_mission,
            default_cadences,
            seconds_until_next_mission,
        )

        cadences = {c.name: c for c in default_cadences()}
        assert "mission" in cadences
        assert cadences["mission"].runner is _fire_mission
        assert cadences["mission"].delay_query is seconds_until_next_mission

    @pytest.mark.asyncio
    async def test_fire_mission_noops_without_active_missions(self, db):
        from project_forge.web.lifespan_scheduler import _fire_mission

        await _fire_mission(db)  # must not raise

    @pytest.mark.asyncio
    async def test_fire_mission_generates_for_picked_mission(self, db, monkeypatch):
        import project_forge.engine.mission as mission_mod
        from project_forge.web.lifespan_scheduler import _fire_mission

        m = _mk_mission()
        await db.save_mission(m)
        called: dict = {}

        async def _fake(db_, mission, **kw):
            called["mission_id"] = mission.id
            return None

        monkeypatch.setattr(mission_mod, "generate_mission_idea", _fake)
        await _fire_mission(db)
        assert called["mission_id"] == m.id


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "test_mission_routes.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


_CREATE_BODY = {
    "title": "PKI blind spots",
    "brief": "Find product ideas around certificate lifecycle pain in mid-size enterprises.",
    "urls": ["https://example.com/whitepaper"],
    "category": "micro-saas",
}


class TestMissionRoutes:
    @pytest.mark.asyncio
    async def test_create_and_list(self, client):
        resp = await client.post("/api/missions", json=_CREATE_BODY)
        assert resp.status_code == 200
        created = resp.json()
        assert created["title"] == "PKI blind spots"
        assert created["status"] == "active"

        resp = await client.get("/api/missions")
        assert resp.status_code == 200
        listing = resp.json()["missions"]
        assert len(listing) == 1
        assert listing[0]["id"] == created["id"]
        assert listing[0]["idea_count"] == 0

    @pytest.mark.asyncio
    async def test_create_validation_422(self, client):
        resp = await client.post("/api/missions", json={"title": "x", "brief": "short"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_status_update_and_404(self, client):
        resp = await client.post("/api/missions", json=_CREATE_BODY)
        mission_id = resp.json()["id"]
        resp = await client.post(f"/api/missions/{mission_id}/status", json={"status": "paused"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"
        resp = await client.post("/api/missions/nope/status", json={"status": "paused"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_route(self, client, monkeypatch):
        import project_forge.engine.mission as mission_mod
        from project_forge.engine.mission import MissionGenerationResult

        resp = await client.post("/api/missions", json=_CREATE_BODY)
        mission_id = resp.json()["id"]

        async def _fake(db_, mission, **kw):
            idea = _mk_idea(mission_id=mission.id, generation_mode="mission", content_hash="rt1")
            return MissionGenerationResult(idea=idea, saved=True, reason=None)

        monkeypatch.setattr(mission_mod, "generate_mission_idea", _fake)
        resp = await client.post(f"/api/missions/{mission_id}/generate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["idea"]["name"] == "Mission Idea"
        assert data["saved"] is True

    @pytest.mark.asyncio
    async def test_generate_unknown_404(self, client):
        resp = await client.post("/api/missions/nope/generate")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_generate_archived_409(self, client):
        resp = await client.post("/api/missions", json=_CREATE_BODY)
        mission_id = resp.json()["id"]
        await client.post(f"/api/missions/{mission_id}/status", json={"status": "archived"})
        resp = await client.post(f"/api/missions/{mission_id}/generate")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_generate_rate_limited(self, client, monkeypatch):
        import project_forge.engine.mission as mission_mod
        from project_forge.engine.mission import MissionGenerationResult

        resp = await client.post("/api/missions", json=_CREATE_BODY)
        mission_id = resp.json()["id"]

        async def _fake(db_, mission, **kw):
            idea = _mk_idea(mission_id=mission.id, content_hash=f"rl{_fake.n}")
            _fake.n += 1
            return MissionGenerationResult(idea=idea, saved=True, reason=None)

        _fake.n = 0
        monkeypatch.setattr(mission_mod, "generate_mission_idea", _fake)
        for _ in range(5):
            resp = await client.post(f"/api/missions/{mission_id}/generate")
            assert resp.status_code == 200
        resp = await client.post(f"/api/missions/{mission_id}/generate")
        assert resp.status_code == 429

    @pytest.mark.asyncio
    async def test_mission_ideas_endpoint(self, client):
        from project_forge.web.app import db

        resp = await client.post("/api/missions", json=_CREATE_BODY)
        mission_id = resp.json()["id"]
        await db.save_idea(_mk_idea(name="Linked", mission_id=mission_id, content_hash="li1"))
        await db.save_idea(_mk_idea(name="Unlinked", content_hash="li2"))
        resp = await client.get(f"/api/missions/{mission_id}/ideas")
        assert resp.status_code == 200
        names = [i["name"] for i in resp.json()["ideas"]]
        assert names == ["Linked"]

    @pytest.mark.asyncio
    async def test_page_renders(self, client):
        await client.post("/api/missions", json=_CREATE_BODY)
        resp = await client.get("/missions")
        assert resp.status_code == 200
        assert "PKI blind spots" in resp.text
