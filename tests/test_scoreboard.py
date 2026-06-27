"""Tests for the Scoreboard avenue (v0.17) — the autonomous LEARN loop.

The engine predicts (fundability / ambition / snipe) but never checks whether
it was right. The Scoreboard captures real outcome signals for its bets and
builds a calibration report: did higher predicted scores actually track higher
realized signal? Surfaces recommendations; recalibration stays human-gated.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.engine import scoreboard
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "scoreboard.db")
    await database.connect()
    yield database
    await database.close()


def _snipe_idea(name, incumbent, snipe, **over) -> Idea:
    idea = Idea(
        name=name,
        tagline=f"tag {name}",
        description="d" * 60,
        category=IdeaCategory.MICRO_SAAS,
        market_analysis="m" * 40,
        feasibility_score=0.7,
        mvp_scope="mvp" * 5,
        tech_stack=["python"],
        **over,
    )
    idea.snipe_score = snipe
    idea.target_incumbent = incumbent
    idea.generation_mode = "snipe"
    return idea


class TestCapture:
    @pytest.mark.asyncio
    async def test_records_signal_for_snipe_ideas(self, db):
        await db.save_idea(_snipe_idea("A", "Calendly", 0.9, content_hash="a"))
        await db.save_idea(_snipe_idea("B", "Okta", 0.4, content_hash="b"))
        stars = {"Calendly": 30000, "Okta": 800}
        res = await scoreboard.capture_outcome_signals(
            db,
            gh_stars=lambda name: stars.get(name),
        )
        assert res["captured"] == 2
        rows = await scoreboard.read_signals(db)
        by_ref = {r["entity_ref"]: r for r in rows}
        assert by_ref["Calendly"]["value"] == 30000
        assert by_ref["Calendly"]["predicted"] == 0.9
        assert by_ref["Calendly"]["axis"] == "snipe"

    @pytest.mark.asyncio
    async def test_skips_when_fetcher_returns_none(self, db):
        await db.save_idea(_snipe_idea("A", "Calendly", 0.9, content_hash="a"))
        res = await scoreboard.capture_outcome_signals(db, gh_stars=lambda name: None)
        assert res["captured"] == 0

    @pytest.mark.asyncio
    async def test_degrades_when_fetcher_raises(self, db):
        await db.save_idea(_snipe_idea("A", "Calendly", 0.9, content_hash="a"))

        def _boom(name):
            raise OSError("network down")

        res = await scoreboard.capture_outcome_signals(db, gh_stars=_boom)
        assert res["captured"] == 0  # never raises


class TestCalibration:
    @pytest.mark.asyncio
    async def test_direction_aligned_when_high_predicts_high(self, db):
        # High snipe scores → high realized stars; low → low. Aligned.
        await db.save_idea(_snipe_idea("hi", "Calendly", 0.9, content_hash="h"))
        await db.save_idea(_snipe_idea("lo", "Okta", 0.3, content_hash="l"))
        await scoreboard.capture_outcome_signals(
            db,
            gh_stars=lambda n: {"Calendly": 30000, "Okta": 200}.get(n),
        )
        cal = await scoreboard.build_calibration(db)
        assert "snipe" in cal["axes"]
        assert cal["axes"]["snipe"]["n"] == 2
        assert cal["axes"]["snipe"]["direction"] == "aligned"

    @pytest.mark.asyncio
    async def test_direction_inverted_flags_a_recommendation(self, db):
        # High predicted → LOW realized: the engine is miscalibrated. Inverted.
        await db.save_idea(_snipe_idea("hi", "Calendly", 0.9, content_hash="h"))
        await db.save_idea(_snipe_idea("lo", "Okta", 0.3, content_hash="l"))
        await scoreboard.capture_outcome_signals(
            db,
            gh_stars=lambda n: {"Calendly": 100, "Okta": 9000}.get(n),
        )
        cal = await scoreboard.build_calibration(db)
        assert cal["axes"]["snipe"]["direction"] == "inverted"
        assert cal["recommendations"]

    @pytest.mark.asyncio
    async def test_empty_corpus_is_safe(self, db):
        cal = await scoreboard.build_calibration(db)
        assert cal["axes"] == {}
        assert isinstance(cal["recommendations"], list)

    @pytest.mark.asyncio
    async def test_markdown_renders(self, db):
        await db.save_idea(_snipe_idea("hi", "Calendly", 0.9, content_hash="h"))
        await scoreboard.capture_outcome_signals(db, gh_stars=lambda n: 30000)
        cal = await scoreboard.build_calibration(db)
        md = scoreboard.format_calibration_markdown(cal)
        assert isinstance(md, str) and len(md) > 0


class TestRoutes:
    @pytest_asyncio.fixture
    async def client(self, tmp_path):
        from project_forge.web.app import app
        from project_forge.web.app import db as appdb

        appdb.db_path = tmp_path / "sb_routes.db"
        await appdb.connect()
        await appdb.save_idea(_snipe_idea("A", "Calendly", 0.9, content_hash="a"))
        await scoreboard.capture_outcome_signals(appdb, gh_stars=lambda n: 30000)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
        await appdb.close()

    @pytest.mark.asyncio
    async def test_api_scoreboard_returns_calibration(self, client):
        resp = await client.get("/api/scoreboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "axes" in data and "recommendations" in data

    @pytest.mark.asyncio
    async def test_scoreboard_page_renders(self, client):
        resp = await client.get("/scoreboard")
        assert resp.status_code == 200
        assert "scoreboard" in resp.text.lower()
