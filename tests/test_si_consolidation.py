"""Tests for Think Tank consolidation: archive [SUPER] junk + dedupe base."""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.si_consolidation import consolidate_self_improvement
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "si_consol.db")
    await database.connect()
    yield database
    await database.close()


def _si(name: str, ch: str, status="new") -> Idea:
    idea = Idea(
        name=name,
        tagline="t " + name,
        description="d" * 40,
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="internal quality",
        feasibility_score=0.8,
        mvp_scope="change files",
        tech_stack=["python"],
        content_hash=ch,
    )
    idea.status = status
    return idea


async def _status(db, idea_id):
    got = await db.get_idea(idea_id)
    return got.status


class TestConsolidate:
    @pytest.mark.asyncio
    async def test_archives_super_and_dupes_keeps_base(self, db):
        supers = [_si(f"[SUPER] Floaty {i}", f"s{i}") for i in range(5)]
        base_a = _si("Churn Endpoint Rate Limit", "b1")
        base_a_dupe = _si("churn endpoint rate limit", "b2")  # same after normalize
        base_b = _si("Add tests for scoreboard", "b3")
        for idea in [*supers, base_a, base_a_dupe, base_b]:
            await db.save_idea(idea)

        report = await consolidate_self_improvement(db)
        assert report["archived_super"] == 5
        assert report["archived_dupes"] == 1
        assert report["kept"] == 2  # base_a + base_b

        for s in supers:
            assert await _status(db, s.id) == "archived"
        # One of the dupes archived, the freshest kept.
        statuses = {await _status(db, base_a.id), await _status(db, base_a_dupe.id)}
        assert statuses == {"new", "archived"}
        assert await _status(db, base_b.id) == "new"

    @pytest.mark.asyncio
    async def test_archives_garbled_crossover_names(self, db):
        garbled = [
            _si("Dashboard Ux Improvements And for Performance", "g1"),
            _si("Ci Pipeline Gap Detection for Reliability", "g2"),
            _si("Security Hardening Of Api for Reliability", "g3"),
        ]
        clean = _si("Churn Endpoint Rate Limit", "c1")
        for idea in [*garbled, clean]:
            await db.save_idea(idea)
        report = await consolidate_self_improvement(db)
        assert report["archived_garbled"] == 3
        assert report["kept"] == 1
        for g in garbled:
            assert await _status(db, g.id) == "archived"
        assert await _status(db, clean.id) == "new"

    @pytest.mark.asyncio
    async def test_idempotent(self, db):
        await db.save_idea(_si("[SUPER] junk", "s1"))
        await db.save_idea(_si("Real Fix", "b1"))
        first = await consolidate_self_improvement(db)
        second = await consolidate_self_improvement(db)
        assert first["archived_super"] == 1
        assert second["archived_super"] == 0  # already archived
        assert second["kept"] == 1

    @pytest.mark.asyncio
    async def test_leaves_non_si_untouched(self, db):
        other = Idea(
            name="[SUPER] Real Product Combo",
            tagline="t",
            description="d" * 40,
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="m",
            feasibility_score=0.7,
            mvp_scope="s",
            tech_stack=["python"],
            content_hash="o1",
        )
        await db.save_idea(other)
        await consolidate_self_improvement(db)
        assert await _status(db, other.id) == "new"  # not self-improvement → untouched
