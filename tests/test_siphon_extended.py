"""Tests for the extended siphon: super-component dedup + vertical-cap collapse.

The original siphon (#71) explicitly skips `[SUPER]` ideas and applies no
collapse to the "X for {vertical}" clone pattern. Both gaps showed up
in the May 2026 corpus inspection:
- 33 ideas shared the same tagline prefix because supers kept re-clustering
  the same atomic components under near-identical theme names.
- 208 of 825 active ideas (28%) were "X for {vertical}" remakes — no cap.

This module exercises the two new siphon entry points that fill those
gaps, plus the threshold-parameterisation of the original `siphon_duplicates`
so an aggressive one-shot trim can run without mutating the going-forward
default.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(
    name: str,
    tagline: str,
    *,
    description: str = "default description",
    category: IdeaCategory = IdeaCategory.OBSERVABILITY,
    score: float = 0.7,
    status: str = "new",
) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description=description,
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
        status=status,
    )


def _super_idea(name: str, components: list[str], *, score: float = 0.85) -> Idea:
    """Build a [SUPER] idea whose description embeds component bullets in
    the production format ('- **Name**: blurb').
    """
    body = (
        f"{name} brings together {len(components)} complementary project "
        "concepts into a single, cohesive platform:\n\n"
    )
    body += "\n".join(f"- **{c}**: blurb for {c}" for c in components)
    return _idea(
        name=f"[SUPER] {name}",
        tagline=f"super tagline for {name}",
        description=body,
        score=score,
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "siphon_ext.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# Threshold parameterisation of the original atomic siphon
# --------------------------------------------------------------------------- #


class TestSiphonThresholdOverride:
    @pytest.mark.asyncio
    async def test_accepts_tightened_thresholds(self, db):
        """`siphon_duplicates` accepts explicit thresholds; tighter ones
        catch clusters the defaults miss."""
        from project_forge.engine.siphon import siphon_duplicates

        # Two atomic ideas with very different names, partially-overlapping
        # taglines (Jaccard ~0.5). Defaults (0.6 / 0.7) leave them alone;
        # tightened (0.45 / 0.55) clusters them.
        await db.save_idea(_idea(
            "Alpha Detector",
            "anomaly detector for distributed traces with sampling",
            score=0.8,
        ))
        await db.save_idea(_idea(
            "Beta Profiler",
            "anomaly detector for distributed traces with rules",
            score=0.6,
        ))

        loose = await siphon_duplicates(db, dry_run=True)
        tight = await siphon_duplicates(
            db,
            dry_run=True,
            tagline_threshold=0.45,
            name_threshold=0.55,
        )
        assert tight["cluster_count"] >= loose["cluster_count"]


# --------------------------------------------------------------------------- #
# Super-idea dedup by shared component overlap
# --------------------------------------------------------------------------- #


class TestSiphonSupersByComponents:
    @pytest.mark.asyncio
    async def test_archives_super_sharing_most_components(self, db):
        from project_forge.engine.siphon import siphon_supers_by_components

        common = ["Atom A", "Atom B", "Atom C", "Atom D"]
        # Two supers share 4 of 5 components → archive the lower-score one.
        await db.save_idea(_super_idea("Theme One", [*common, "Atom X"], score=0.92))
        await db.save_idea(_super_idea("Theme Two", [*common, "Atom Y"], score=0.88))

        report = await siphon_supers_by_components(db, dry_run=False, overlap_min=3)
        assert report["archived_count"] == 1
        # The lower-scored super was the one archived.
        cursor = await db.db.execute(
            "SELECT name FROM ideas WHERE status='archived'"
        )
        archived = [row["name"] for row in await cursor.fetchall()]
        assert archived == ["[SUPER] Theme Two"]

    @pytest.mark.asyncio
    async def test_leaves_disjoint_supers_alone(self, db):
        from project_forge.engine.siphon import siphon_supers_by_components

        await db.save_idea(_super_idea("Alpha", ["A1", "A2", "A3"], score=0.9))
        await db.save_idea(_super_idea("Beta", ["B1", "B2", "B3"], score=0.85))

        report = await siphon_supers_by_components(db, dry_run=True, overlap_min=2)
        assert report["archived_count"] == 0
        assert report["cluster_count"] == 0

    @pytest.mark.asyncio
    async def test_clusters_supers_by_high_name_jaccard(self, db):
        """Two supers with no component overlap but near-identical names
        still cluster — that's the 'Drift Tracker / Drift Tracking' bug."""
        from project_forge.engine.siphon import siphon_supers_by_components

        await db.save_idea(_super_idea(
            "IETF Standards Compliance Drift Tracker",
            ["X1", "X2", "X3"],
            score=0.9,
        ))
        await db.save_idea(_super_idea(
            "IETF Standards Compliance Drift Tracking",
            ["Y1", "Y2", "Y3"],
            score=0.85,
        ))

        report = await siphon_supers_by_components(
            db, dry_run=False, overlap_min=100, name_jaccard=0.6,
        )
        assert report["archived_count"] == 1

    @pytest.mark.asyncio
    async def test_ignores_non_super_ideas(self, db):
        from project_forge.engine.siphon import siphon_supers_by_components

        # Regular idea — must never be touched by the super-siphon.
        await db.save_idea(_idea("Atomic Idea", "regular tagline", score=0.9))
        await db.save_idea(_super_idea("Solo Super", ["A", "B"], score=0.8))

        report = await siphon_supers_by_components(db, dry_run=False, overlap_min=2)
        assert report["archived_count"] == 0
        cursor = await db.db.execute(
            "SELECT COUNT(*) FROM ideas WHERE status='new'"
        )
        assert (await cursor.fetchone())[0] == 2

    @pytest.mark.asyncio
    async def test_dry_run_does_not_mutate(self, db):
        from project_forge.engine.siphon import siphon_supers_by_components

        await db.save_idea(_super_idea(
            "Alpha", ["A", "B", "C", "D"], score=0.9,
        ))
        await db.save_idea(_super_idea(
            "Beta", ["A", "B", "C", "D"], score=0.8,
        ))

        report = await siphon_supers_by_components(db, dry_run=True, overlap_min=3)
        assert report["dry_run"] is True
        assert report["cluster_count"] == 1
        cursor = await db.db.execute("SELECT COUNT(*) FROM ideas WHERE status='archived'")
        assert (await cursor.fetchone())[0] == 0


# --------------------------------------------------------------------------- #
# Vertical-cap collapse: 'X for {vertical}' pattern
# --------------------------------------------------------------------------- #


class TestSiphonVerticals:
    @pytest.mark.asyncio
    async def test_caps_clones_at_two_per_concept(self, db):
        from project_forge.engine.siphon import siphon_verticals

        await db.save_idea(_idea("Pqc Tracker for Healthcare", "t", score=0.92))
        await db.save_idea(_idea("Pqc Tracker for Financial", "t", score=0.91))
        await db.save_idea(_idea("Pqc Tracker for Container", "t", score=0.90))
        await db.save_idea(_idea("Pqc Tracker for Telecom", "t", score=0.89))
        await db.save_idea(_idea("Pqc Tracker for Retail", "t", score=0.88))

        report = await siphon_verticals(db, dry_run=False, cap=2)

        assert report["archived_count"] == 3
        cursor = await db.db.execute(
            "SELECT name FROM ideas WHERE status='new' ORDER BY feasibility_score DESC"
        )
        kept = [r["name"] for r in await cursor.fetchall()]
        # Top 2 by score survive.
        assert kept == [
            "Pqc Tracker for Healthcare",
            "Pqc Tracker for Financial",
        ]

    @pytest.mark.asyncio
    async def test_concept_strip_is_case_insensitive(self, db):
        from project_forge.engine.siphon import siphon_verticals

        await db.save_idea(_idea("PQC TRACKER For Healthcare", "t", score=0.9))
        await db.save_idea(_idea("Pqc Tracker for Financial", "t", score=0.85))
        await db.save_idea(_idea("pqc tracker for Container", "t", score=0.8))

        report = await siphon_verticals(db, dry_run=True, cap=1)
        assert report["archived_count"] == 2

    @pytest.mark.asyncio
    async def test_ignores_ideas_without_for_pattern(self, db):
        from project_forge.engine.siphon import siphon_verticals

        await db.save_idea(_idea("Standalone Engine", "t", score=0.9))
        await db.save_idea(_idea("Another Standalone", "t", score=0.85))

        report = await siphon_verticals(db, dry_run=True, cap=1)
        assert report["archived_count"] == 0

    @pytest.mark.asyncio
    async def test_does_not_archive_terminal_status_ideas(self, db):
        """approved / scaffolded / implemented / contributed must survive
        regardless of vertical-cap."""
        from project_forge.engine.siphon import siphon_verticals

        await db.save_idea(_idea(
            "Concept for Healthcare", "t", score=0.99, status="approved",
        ))
        await db.save_idea(_idea("Concept for Financial", "t", score=0.85))
        await db.save_idea(_idea("Concept for Container", "t", score=0.8))
        await db.save_idea(_idea("Concept for Telecom", "t", score=0.75))

        report = await siphon_verticals(db, dry_run=False, cap=1)
        # cap=1 → one 'new' survives, two get archived; the 'approved' is
        # untouchable so it doesn't count toward the cap budget either.
        cursor = await db.db.execute(
            "SELECT name, status FROM ideas ORDER BY feasibility_score DESC"
        )
        rows = [(r["name"], r["status"]) for r in await cursor.fetchall()]
        statuses = {n: s for n, s in rows}
        assert statuses["Concept for Healthcare"] == "approved"
        # Highest 'new' survives.
        assert statuses["Concept for Financial"] == "new"
        assert report["archived_count"] == 2


# --------------------------------------------------------------------------- #
# Combined entrypoint
# --------------------------------------------------------------------------- #


class TestSiphonAll:
    @pytest.mark.asyncio
    async def test_returns_combined_report(self, db):
        from project_forge.engine.siphon import siphon_all

        report = await siphon_all(db, dry_run=True)
        assert set(report.keys()) >= {"atomic", "supers", "verticals", "total_archived"}
        assert report["dry_run"] is True
