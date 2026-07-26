"""Tests for cross-category dedup (#98) — the trampling guard.

Both the INSERT-time similarity gate and the retro siphon compared ideas
WITHIN one category only, so the same idea could live in micro-saas AND
automation-income forever ("cardinality explosion detector" x7 across
two categories in the live corpus). This suite pins the two new guards:

  - should_accept check 5: tagline >= 0.80 vs an idea in ANOTHER
    category hard-rejects. Tagline-only — name similarity stays
    same-category-scoped by deliberate contract
    (test_dedup_uniqueness.test_name_check_scoped_to_same_category).
  - siphon_cross_category: retro pass clustering taglines across
    categories (>= 0.60), keeping only >=2-category clusters, archiving
    losers reversibly as cross_category_dedup.
  - the siphon cadence is now DAILY (the weekly fire lost 7:1 to hourly
    generation; pool oscillated 1.6k -> 2.8k between passes).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "crossdedup.db")
    await database.connect()
    yield database
    await database.close()


def _idea(name: str, tagline: str, category: IdeaCategory, **over) -> Idea:
    base = dict(
        name=name,
        tagline=tagline,
        description="A concrete tool that does one job for one operator well.",
        category=category,
        market_analysis="A specific buyer exists.",
        feasibility_score=0.7,
        mvp_scope="Phase 1: core.",
        tech_stack=["python"],
        content_hash=f"cc-{name.lower().replace(' ', '-')[:40]}",
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# INSERT-time gate                                                            #
# --------------------------------------------------------------------------- #


class TestInsertGate:
    @pytest.mark.asyncio
    async def test_near_identical_tagline_other_category_rejected(self, db):
        from project_forge.engine.dedup import should_accept

        await db.save_idea(
            _idea(
                "Cardinality Guard",
                "cardinality explosion detector for prometheus metrics",
                IdeaCategory.OBSERVABILITY,
            )
        )
        cand = _idea(
            "Metric Blowup Watch",
            "cardinality explosion detector for prometheus metrics dashboards",
            IdeaCategory.DEVOPS_TOOLING,
        )
        accepted, reason = await should_accept(cand, db)
        assert accepted is False
        assert "cross_category" in (reason or "")

    @pytest.mark.asyncio
    async def test_moderately_similar_other_category_accepted(self, db):
        from project_forge.engine.dedup import should_accept

        await db.save_idea(
            _idea(
                "Cardinality Guard",
                "cardinality explosion detector for prometheus metrics",
                IdeaCategory.OBSERVABILITY,
            )
        )
        cand = _idea(
            "Alert Budgeter",
            "alerting budget planner for grafana dashboards and oncall",
            IdeaCategory.DEVOPS_TOOLING,
        )
        accepted, _ = await should_accept(cand, db)
        assert accepted is True

    @pytest.mark.asyncio
    async def test_name_scoping_contract_preserved(self, db):
        """Same name tokens, different category, different taglines — must
        still be ACCEPTED (the deliberate same-category name-scoping from
        test_dedup_uniqueness stays intact)."""
        from project_forge.engine.dedup import should_accept

        await db.save_idea(
            _idea(
                "Distributed Tracing Anomaly Detector",
                "trace anomaly scoring for microservice fleets",
                IdeaCategory.OBSERVABILITY,
            )
        )
        cand = _idea(
            "Tracing Anomaly Detector Distributed",
            "pii-safe span scrubbing before traces leave the cluster",
            IdeaCategory.PRIVACY,
        )
        accepted, _ = await should_accept(cand, db)
        assert accepted is True


# --------------------------------------------------------------------------- #
# retro siphon pass                                                           #
# --------------------------------------------------------------------------- #


def _pair(db_none=None):
    """Two near-same taglines in different categories; the OBSERVABILITY
    one carries the stronger composite (fundability) and must be kept."""
    keep = _idea(
        "SDK Exfil Watch",
        "third-party sdk data exfiltration detector for mobile apps",
        IdeaCategory.OBSERVABILITY,
        content_hash="cc-keep",
    )
    keep.fundability_score = 0.9
    keep.generation_mode = "novel"
    lose = _idea(
        "SDK Leak Finder",
        "third-party sdk data exfiltration detector for mobile games",
        IdeaCategory.PRIVACY,
        content_hash="cc-lose",
    )
    lose.fundability_score = 0.2
    return keep, lose


class TestSiphonCrossCategory:
    @pytest.mark.asyncio
    async def test_archives_cross_category_loser_keeps_best(self, db):
        from project_forge.engine.siphon import siphon_cross_category

        keep, lose = _pair()
        await db.save_idea(keep)
        await db.save_idea(lose)
        report = await siphon_cross_category(db, dry_run=False)
        assert report["archived_count"] == 1

        cur = await db.db.execute("SELECT status, archived_reason FROM ideas WHERE content_hash = 'cc-lose'")
        r = await cur.fetchone()
        assert r["status"] == "archived"
        assert r["archived_reason"] == "cross_category_dedup"
        cur = await db.db.execute("SELECT status FROM ideas WHERE content_hash = 'cc-keep'")
        assert (await cur.fetchone())["status"] == "new"

    @pytest.mark.asyncio
    async def test_single_category_cluster_not_touched(self, db):
        """Within-category paraphrases are the atomic pass's job — this
        pass only acts on clusters spanning >= 2 categories."""
        from project_forge.engine.siphon import siphon_cross_category

        a = _idea(
            "Dup One",
            "webhook replay debugger for stripe events",
            IdeaCategory.MICRO_SAAS,
            content_hash="cc-s1",
        )
        b = _idea(
            "Dup Two",
            "webhook replay debugger for stripe event streams",
            IdeaCategory.MICRO_SAAS,
            content_hash="cc-s2",
        )
        await db.save_idea(a)
        await db.save_idea(b)
        report = await siphon_cross_category(db, dry_run=False)
        assert report["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_si_and_supers_excluded(self, db):
        from project_forge.engine.siphon import siphon_cross_category

        si = _idea(
            "Decompose routes",
            "third-party sdk data exfiltration detector for mobile apps",
            IdeaCategory.SELF_IMPROVEMENT,
            content_hash="cc-si",
        )
        sup = _idea(
            "[SUPER] Mega Bundle",
            "third-party sdk data exfiltration detector for mobile games",
            IdeaCategory.PRIVACY,
            content_hash="cc-sup",
        )
        await db.save_idea(si)
        await db.save_idea(sup)
        report = await siphon_cross_category(db, dry_run=False)
        assert report["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_dry_run_mutates_nothing(self, db):
        from project_forge.engine.siphon import siphon_cross_category

        keep, lose = _pair()
        await db.save_idea(keep)
        await db.save_idea(lose)
        report = await siphon_cross_category(db, dry_run=True)
        assert report["archived_count"] == 1
        assert report["applied_count"] == 0
        cur = await db.db.execute("SELECT status FROM ideas WHERE content_hash = 'cc-lose'")
        assert (await cur.fetchone())["status"] == "new"

    @pytest.mark.asyncio
    async def test_reversible_via_restore(self, db):
        from project_forge.engine.siphon import restore_dedup_archive, siphon_cross_category

        keep, lose = _pair()
        await db.save_idea(keep)
        await db.save_idea(lose)
        await siphon_cross_category(db, dry_run=False)
        restored = await restore_dedup_archive(db)
        assert restored == 1
        cur = await db.db.execute("SELECT status FROM ideas WHERE content_hash = 'cc-lose'")
        assert (await cur.fetchone())["status"] == "new"

    @pytest.mark.asyncio
    async def test_siphon_all_includes_cross_pass(self, db):
        from project_forge.engine.siphon import siphon_all

        report = await siphon_all(db, dry_run=True)
        assert "cross" in report
        assert set(report.keys()) >= {
            "atomic",
            "supers",
            "verticals",
            "cross",
            "density",
            "total_archived",
        }


# --------------------------------------------------------------------------- #
# cadence                                                                     #
# --------------------------------------------------------------------------- #


class TestDailySiphon:
    def test_siphon_interval_default_is_daily(self):
        from project_forge.web.lifespan_scheduler import default_cadences

        siphon = next(c for c in default_cadences() if c.name == "siphon")
        assert siphon.interval == timedelta(hours=24)
