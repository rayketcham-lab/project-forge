"""TDD: idea siphon (#71) — retroactive dedup + tighter going-forward gate.

Two modes:
- dry_run=True: return a report (clusters + which idea would be kept,
  which archived) without touching the DB.
- dry_run=False: perform the archives, set archived_reason / archived_at,
  write audit log rows.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, tagline: str, *,
          category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
          score: float = 0.7) -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "siphon.db")
    await d.connect()
    yield d
    await d.close()


# ── Cluster detection ────────────────────────────────────────────────


class TestClusterDetection:
    @pytest.mark.asyncio
    async def test_finds_paraphrase_cluster(self, db):
        from project_forge.engine.siphon import find_duplicate_clusters

        await db.save_idea(_idea("Cert Pin Detector",
                                 "certificate pinning misconfiguration scanner"))
        await db.save_idea(_idea("Pinning Misconfig Scanner",
                                 "certificate pinning misconfiguration detector"))
        await db.save_idea(_idea("Unrelated Tool",
                                 "post-quantum migration playbook engine"))

        clusters = await find_duplicate_clusters(db)

        # 1 cluster of 2; the unrelated tool is its own cluster (or excluded).
        cluster_sizes = sorted(len(c) for c in clusters if len(c) > 1)
        assert cluster_sizes == [2], (
            f"Expected one 2-idea cluster; got sizes {cluster_sizes}"
        )

    @pytest.mark.asyncio
    async def test_separates_by_category(self, db):
        """Identical taglines across DIFFERENT categories are NOT clustered.
        Each category bucket is scanned independently."""
        from project_forge.engine.siphon import find_duplicate_clusters

        same = "supply chain attack detector"
        await db.save_idea(_idea("ST", same, category=IdeaCategory.SECURITY_TOOL))
        await db.save_idea(_idea("VR", same, category=IdeaCategory.VULNERABILITY_RESEARCH))

        clusters = await find_duplicate_clusters(db)
        # No cross-category clustering — both ideas survive
        cluster_sizes = sorted(len(c) for c in clusters if len(c) > 1)
        assert cluster_sizes == [], (
            f"Cross-category match was clustered (shouldn't be): {clusters}"
        )

    @pytest.mark.asyncio
    async def test_transitive_clustering(self, db):
        """A ~ B and B ~ C → all three in one cluster."""
        from project_forge.engine.siphon import find_duplicate_clusters

        await db.save_idea(_idea("A", "supply chain attack detection automation"))
        await db.save_idea(_idea("B", "supply chain attack detection orchestration"))
        await db.save_idea(_idea("C", "supply chain attack detection workflow"))

        clusters = await find_duplicate_clusters(db)
        big = [c for c in clusters if len(c) >= 3]
        assert len(big) == 1, (
            f"Expected one transitive cluster of 3; got {[len(c) for c in clusters]}"
        )

    @pytest.mark.asyncio
    async def test_archived_ideas_excluded(self, db):
        """Already-archived ideas don't pollute cluster results."""
        from project_forge.engine.siphon import find_duplicate_clusters

        a = _idea("A", "api key rotation automation tool")
        b = _idea("B", "api key rotation automation engine")
        await db.save_idea(a)
        await db.save_idea(b)
        await db.update_idea_status(a.id, "archived")

        clusters = await find_duplicate_clusters(db)
        assert all(len(c) <= 1 for c in clusters), (
            "Archived ideas leaked into clusters"
        )


# ── Pick-the-survivor logic ──────────────────────────────────────────


class TestSurvivorChoice:
    @pytest.mark.asyncio
    async def test_keeps_highest_feasibility(self, db):
        from project_forge.engine.siphon import siphon_duplicates

        a = _idea("A", "secrets sprawl scanner across repos", score=0.65)
        b = _idea("B", "secrets sprawl scanner across repos", score=0.85)
        c = _idea("C", "secrets sprawl scanner across repos", score=0.70)
        await db.save_idea(a)
        await db.save_idea(b)
        await db.save_idea(c)

        report = await siphon_duplicates(db, dry_run=True)
        clusters = report["clusters"]
        target = [c for c in clusters if len(c["members"]) == 3][0]
        assert target["keep"] == b.id, (
            f"Should keep B (highest score 0.85); kept {target['keep']}"
        )
        assert set(target["archive"]) == {a.id, c.id}


# ── Dry-run vs apply ─────────────────────────────────────────────────


class TestDryRunVsApply:
    @pytest.mark.asyncio
    async def test_dry_run_does_not_mutate(self, db):
        from project_forge.engine.siphon import siphon_duplicates

        a = _idea("A", "lateral movement detection in containers", score=0.6)
        b = _idea("B", "lateral movement detection in containers", score=0.8)
        await db.save_idea(a)
        await db.save_idea(b)

        await siphon_duplicates(db, dry_run=True)

        fresh_a = await db.get_idea(a.id)
        fresh_b = await db.get_idea(b.id)
        assert fresh_a.status == "new"
        assert fresh_b.status == "new"

    @pytest.mark.asyncio
    async def test_apply_archives_loser(self, db):
        from project_forge.engine.siphon import siphon_duplicates

        a = _idea("A", "lateral movement detection in containers", score=0.6)
        b = _idea("B", "lateral movement detection in containers", score=0.8)
        await db.save_idea(a)
        await db.save_idea(b)

        report = await siphon_duplicates(db, dry_run=False)

        assert report["archived_count"] == 1
        fresh_a = await db.get_idea(a.id)
        fresh_b = await db.get_idea(b.id)
        assert fresh_a.status == "archived"
        assert fresh_b.status == "new"

    @pytest.mark.asyncio
    async def test_apply_idempotent(self, db):
        """Running siphon twice doesn't double-archive."""
        from project_forge.engine.siphon import siphon_duplicates

        a = _idea("A", "OAuth scope minimization tool", score=0.6)
        b = _idea("B", "OAuth scope minimization tool", score=0.8)
        await db.save_idea(a)
        await db.save_idea(b)

        first = await siphon_duplicates(db, dry_run=False)
        second = await siphon_duplicates(db, dry_run=False)

        assert first["archived_count"] == 1
        assert second["archived_count"] == 0


# ── Going-forward tightening ─────────────────────────────────────────


class TestGoingForwardThreshold:
    @pytest.mark.asyncio
    async def test_restore_undoes_only_siphon_archives(self, db):
        """restore_dedup_archive must restore ideas archived BY siphon
        (archived_reason='retroactive_dedup') and leave manually-archived
        ideas alone."""
        from project_forge.engine.siphon import (
            restore_dedup_archive,
            siphon_duplicates,
        )

        # Two near-duplicates → one will get siphoned
        a = _idea("A", "kubernetes secret rotation tool", score=0.6)
        b = _idea("B", "kubernetes secret rotation tool", score=0.8)
        await db.save_idea(a)
        await db.save_idea(b)

        # A manually-archived idea (different reason)
        c = _idea("C", "totally separate concept here", score=0.7)
        await db.save_idea(c)
        await db.update_idea_status(c.id, "archived")

        await siphon_duplicates(db, dry_run=False)

        # A is now archived by siphon, C is manually archived
        before_a = await db.get_idea(a.id)
        before_c = await db.get_idea(c.id)
        assert before_a.status == "archived"
        assert before_c.status == "archived"

        restored = await restore_dedup_archive(db)
        assert restored == 1, f"Should restore exactly the siphon-archived row, got {restored}"

        after_a = await db.get_idea(a.id)
        after_c = await db.get_idea(c.id)
        # A back to new
        assert after_a.status == "new"
        # C still archived (not touched — different reason)
        assert after_c.status == "archived"

    @pytest.mark.asyncio
    async def test_threshold_catches_near_paraphrase(self, db, monkeypatch):
        """At the tightened threshold of the day, a near-paraphrase pair
        with high tagline overlap must be rejected.

        Threshold history: 0.7 (initial) → 0.6 (#71) → 0.72 (v0.11.1 after
        the gates choked generation). The assertion needs to express the
        *intent* — 'this near-paraphrase pair is rejected' — not pin a
        specific number. We monkeypatch the threshold here so the test
        pins the contract, not the configuration."""
        from project_forge.engine import dedup as _dedup
        from project_forge.engine.dedup import should_accept

        # Pin to a tight value for this test regardless of the running default.
        monkeypatch.setattr(_dedup, "SIMILARITY_THRESHOLD", 0.6)

        existing = _idea("Existing", "container image provenance verification scanner")
        await db.save_idea(existing)
        candidate = _idea("Candidate", "container image provenance verification engine")
        ok, reason = await should_accept(candidate, db)
        assert ok is False, (
            f"Tighter threshold did not catch a near-paraphrase. reason={reason}"
        )
