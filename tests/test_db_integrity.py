"""TDD: Database integrity gate — schema lock, integrity audit, required indexes.

Self-improvement (issue #55) is about to mine filtered_ideas (23k+ rows) for
saturation patterns and drive surgical generation patches. SI cannot trust
those signals if the data is rotten.

This test file locks the schema, verifies indexes, and asserts that the
new Database.verify_integrity() audit catches:
- Orphaned filtered_ideas.similar_to_id (target deleted)
- Duplicate active content_hash (uniqueness violation)
- Active super-idea base-name collisions (dedup escapees)

Closes #53.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.models import FilteredIdea, Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(
    name: str,
    *,
    category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
    score: float = 0.82,
    content_hash: str | None = None,
) -> Idea:
    i = Idea(
        name=name,
        tagline=f"tag for {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )
    if content_hash is not None:
        i.content_hash = content_hash
    return i


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "integrity.db")
    await d.connect()
    yield d
    await d.close()


# ── required indexes ──────────────────────────────────────────────────


class TestRequiredIndexes:
    """Telemetry queries over filtered_ideas need time + similar-to indexes."""

    @pytest.mark.asyncio
    async def test_filtered_at_index_exists(self, db):
        cursor = await db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='filtered_ideas'",
        )
        rows = await cursor.fetchall()
        names = {r[0] for r in rows}
        assert "idx_filtered_filtered_at" in names, (
            f"Missing idx_filtered_filtered_at — needed for time-windowed telemetry. Have: {names}"
        )

    @pytest.mark.asyncio
    async def test_filtered_similar_to_index_exists(self, db):
        cursor = await db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='filtered_ideas'",
        )
        rows = await cursor.fetchall()
        names = {r[0] for r in rows}
        assert "idx_filtered_similar_to" in names, (
            f"Missing idx_filtered_similar_to — needed for orphan detection. Have: {names}"
        )

    @pytest.mark.asyncio
    async def test_ideas_status_category_composite_index_exists(self, db):
        cursor = await db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ideas'",
        )
        rows = await cursor.fetchall()
        names = {r[0] for r in rows}
        assert "idx_ideas_status_category" in names, (
            f"Missing composite idx_ideas_status_category — speeds list_super_ideas, counts. Have: {names}"
        )


# ── schema lock ───────────────────────────────────────────────────────


# Snapshot of expected tables. Adding a table requires updating this list
# AND a migration; this guards against accidental schema drift.
EXPECTED_TABLES = frozenset(
    {
        "ideas",
        "generation_runs",
        "used_tuples",
        "category_pair_log",
        "idea_reviews",
        "challenges",
        "filtered_ideas",
        "resources",
        "idea_denials",
        "selection_rounds",
        "repo_registry",
        "route_decisions",
        "outcome_signals",  # v0.17 Scoreboard
        "calibration_weights",  # v0.17 Scoreboard auto-tune
        "missions",  # v0.18 Missions (#84) — operator directives
        "pki_probes",  # v0.23 PKI board — hourly probe log + cadence watermark
    }
)

# Required columns per table. Adding a column requires updating this map.
EXPECTED_COLUMNS = {
    "ideas": frozenset(
        {
            "id",
            "name",
            "tagline",
            "description",
            "category",
            "market_analysis",
            "feasibility_score",
            "mvp_scope",
            "tech_stack",
            "generated_at",
            "status",
            "github_issue_url",
            "project_repo_url",
            "content_hash",
            "source_url",
            # v0.23 PKI board — urgency axis + the concrete artifact a
            # finding is anchored to. Both added by ALTER TABLE migration,
            # so locking them here catches a dropped migration.
            "pki_urgency_score",
            "pki_anchor",
        }
    ),
    "filtered_ideas": frozenset(
        {
            "id",
            "idea_name",
            "idea_tagline",
            "idea_category",
            "filter_reason",
            "original_idea_json",
            "filtered_at",
            "similar_to_id",
        }
    ),
    # Challenges table schema regression (issue #68): SCHEMA literal
    # was extended without a corresponding ALTER TABLE migration, so
    # production DBs were left without these columns and POSTs to
    # /api/ideas/{id}/challenge crashed with "no column named
    # challenge_type". Lock all 11 columns here.
    "challenges": frozenset(
        {
            "id",
            "idea_id",
            "question",
            "challenge_type",
            "focus_area",
            "tone",
            "response",
            "verdict",
            "confidence",
            "changes",
            "created_at",
            # Issue #70: idempotency tracking for the apply-changes endpoint.
            "applied_at",
        }
    ),
}


class TestSchemaLock:
    """Lock the schema so accidental drift fails CI."""

    @pytest.mark.asyncio
    async def test_table_set_matches_snapshot(self, db):
        cursor = await db.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'",
        )
        rows = await cursor.fetchall()
        actual = {r[0] for r in rows}
        missing = EXPECTED_TABLES - actual
        extra = actual - EXPECTED_TABLES
        assert not missing, f"Missing tables: {missing}"
        assert not extra, f"Unexpected tables: {extra}. If intentional, update EXPECTED_TABLES in this test."

    @pytest.mark.asyncio
    async def test_ideas_columns_locked(self, db):
        cursor = await db.db.execute("PRAGMA table_info(ideas)")
        rows = await cursor.fetchall()
        actual = {r[1] for r in rows}
        missing = EXPECTED_COLUMNS["ideas"] - actual
        assert not missing, f"ideas missing columns: {missing}"

    @pytest.mark.asyncio
    async def test_filtered_ideas_columns_locked(self, db):
        cursor = await db.db.execute("PRAGMA table_info(filtered_ideas)")
        rows = await cursor.fetchall()
        actual = {r[1] for r in rows}
        missing = EXPECTED_COLUMNS["filtered_ideas"] - actual
        assert not missing, f"filtered_ideas missing columns: {missing}"

    @pytest.mark.asyncio
    async def test_challenges_columns_locked(self, db):
        """Issue #68: SCHEMA literal had columns the production DB lacked
        because no ALTER TABLE migration was added. CI gate so this never
        repeats — every column declared in db.SCHEMA must be present after
        Database.connect().
        """
        cursor = await db.db.execute("PRAGMA table_info(challenges)")
        rows = await cursor.fetchall()
        actual = {r[1] for r in rows}
        missing = EXPECTED_COLUMNS["challenges"] - actual
        assert not missing, f"challenges missing columns: {missing}"

    @pytest.mark.asyncio
    async def test_existing_db_picks_up_new_columns_via_migration(self, tmp_path):
        """Regression for #68: an existing DB created BEFORE a column was
        added must pick up the new column on next connect(). Simulates
        how the production DB ended up missing challenge_type.
        """
        import aiosqlite

        path = tmp_path / "old_schema.db"
        # Create the DB with the OLD challenges schema (the one prod had).
        async with aiosqlite.connect(path) as old:
            await old.execute("""
                CREATE TABLE challenges (
                    id TEXT PRIMARY KEY,
                    idea_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    response TEXT NOT NULL DEFAULT '',
                    changes TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
            """)
            await old.commit()

        # Now connect via Database — migrations must add the missing columns.
        new_db = Database(path)
        await new_db.connect()
        try:
            cursor = await new_db.db.execute("PRAGMA table_info(challenges)")
            rows = await cursor.fetchall()
            actual = {r[1] for r in rows}
            missing = EXPECTED_COLUMNS["challenges"] - actual
            assert not missing, (
                f"Database.connect() did not migrate the legacy challenges "
                f"table. Missing columns: {missing}. Add ALTER TABLE migrations."
            )
        finally:
            await new_db.close()


# ── verify_integrity ──────────────────────────────────────────────────


class TestVerifyIntegrity:
    """Database.verify_integrity() returns a structured violation report."""

    @pytest.mark.asyncio
    async def test_clean_db_reports_no_violations(self, db):
        report = await db.verify_integrity()

        assert isinstance(report, dict)
        assert "orphaned_filtered_similar_to" in report
        assert "duplicate_active_content_hash" in report
        assert "super_idea_base_collisions" in report
        # All buckets must be empty for a clean DB
        for bucket, items in report.items():
            assert items == [], f"Clean DB had violations in {bucket}: {items}"

    @pytest.mark.asyncio
    async def test_detects_orphaned_filtered_similar_to(self, db):
        # similar_to_id points at a non-existent idea
        fi = FilteredIdea(
            idea_name="orphan ref",
            idea_tagline="t",
            idea_category=IdeaCategory.SECURITY_TOOL,
            filter_reason="duplicate:tagline_similarity",
            original_idea_json="{}",
            similar_to_id="ghost-idea-id-that-does-not-exist",
        )
        await db.save_filtered_idea(fi)

        report = await db.verify_integrity()

        orphans = report["orphaned_filtered_similar_to"]
        assert any(fi.id in entry for entry in orphans), f"Did not detect orphaned similar_to_id. Report: {orphans}"

    @pytest.mark.asyncio
    async def test_does_not_flag_filtered_with_null_similar_to(self, db):
        fi = FilteredIdea(
            idea_name="content hash dup",
            idea_tagline="t",
            idea_category=IdeaCategory.SECURITY_TOOL,
            filter_reason="duplicate:content_hash",
            original_idea_json="{}",
            similar_to_id=None,
        )
        await db.save_filtered_idea(fi)

        report = await db.verify_integrity()
        assert report["orphaned_filtered_similar_to"] == []

    @pytest.mark.asyncio
    async def test_detects_duplicate_active_content_hash(self, db):
        # Two active ideas with the same content_hash — the unique index
        # should prevent this, but if it ever leaks, integrity must catch it.
        a = _idea("Alpha", content_hash="hash-collision-xyz")
        b = _idea("Beta", content_hash="hash-collision-xyz")
        # Bypass the unique index by inserting via raw INSERT OR IGNORE? No —
        # we test detection by directly violating: drop the index, insert both,
        # then call verify_integrity. The check must catch it regardless of index.
        await db.db.execute("DROP INDEX IF EXISTS idx_ideas_content_hash")
        await db.db.commit()

        await db.save_idea(a)
        await db.save_idea(b)

        report = await db.verify_integrity()

        dups = report["duplicate_active_content_hash"]
        assert any("hash-collision-xyz" in entry for entry in dups), (
            f"Did not detect duplicate content_hash. Report: {dups}"
        )

    @pytest.mark.asyncio
    async def test_detects_super_idea_base_name_collision(self, db):
        # Two active super ideas that normalize to the same base
        a = _idea("[SUPER] Certificate Pinning Observatory", score=0.9)
        b = _idea("[SUPER] Certificate Pinning Defense Suite", score=0.91)
        await db.save_idea(a)
        await db.save_idea(b)

        report = await db.verify_integrity()

        collisions = report["super_idea_base_collisions"]
        assert collisions, f"Did not detect super-idea base collision: {collisions}"
        # Should mention both ids or the base name
        joined = " ".join(collisions)
        assert "certificate pinning" in joined.lower()

    @pytest.mark.asyncio
    async def test_archived_supers_dont_trigger_collision(self, db):
        # If one is archived, no collision — only ACTIVE supers should collide.
        a = _idea("[SUPER] Quantum Migration Observatory", score=0.9)
        b = _idea("[SUPER] Quantum Migration Defense Suite", score=0.91)
        await db.save_idea(a)
        await db.save_idea(b)
        await db.db.execute("UPDATE ideas SET status='archived' WHERE id=?", (a.id,))
        await db.db.commit()

        report = await db.verify_integrity()
        assert report["super_idea_base_collisions"] == []
