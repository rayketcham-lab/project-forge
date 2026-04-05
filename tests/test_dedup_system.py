"""Tests for the deduplication system - input tuple tracking and content fingerprinting.

Covers GitHub issues #1 (dedup), #2 (SQL queries), #3 (WAL mode).
"""

from pathlib import Path

import pytest
import pytest_asyncio

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_dedup.db")
    await database.connect()
    yield database
    await database.close()


# === Issue #1: Input-tuple tracking and content fingerprinting ===


class TestContentFingerprint:
    @pytest.mark.asyncio
    async def test_duplicate_content_hash_rejected(self, db):
        """Two ideas with the same content_hash should not both persist."""
        idea1 = Idea(
            id="aaa111",
            name="Test Idea",
            tagline="Test",
            description="Same content",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market",
            feasibility_score=0.8,
            mvp_scope="MVP",
            content_hash="hash_abc123",
        )
        idea2 = Idea(
            id="bbb222",
            name="Test Idea Variant",
            tagline="Test variant",
            description="Same content",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market",
            feasibility_score=0.8,
            mvp_scope="MVP",
            content_hash="hash_abc123",  # Same hash!
        )
        await db.save_idea(idea1)
        await db.save_idea(idea2)  # Should be rejected or replace
        count = await db.count_ideas()
        assert count == 1, "Duplicate content_hash should not create a second row"

    @pytest.mark.asyncio
    async def test_different_content_hash_both_persist(self, db):
        """Ideas with different content_hash should both persist."""
        idea1 = Idea(
            id="aaa111",
            name="Idea A",
            tagline="A",
            description="Content A",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market",
            feasibility_score=0.8,
            mvp_scope="MVP",
            content_hash="hash_aaa",
        )
        idea2 = Idea(
            id="bbb222",
            name="Idea B",
            tagline="B",
            description="Content B",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market",
            feasibility_score=0.8,
            mvp_scope="MVP",
            content_hash="hash_bbb",
        )
        await db.save_idea(idea1)
        await db.save_idea(idea2)
        count = await db.count_ideas()
        assert count == 2


class TestUsedTuples:
    @pytest.mark.asyncio
    async def test_record_used_tuple(self, db):
        """Should be able to record a generation tuple as used."""
        await db.record_used_tuple("security-tool", 0, 2, "basic")
        used = await db.is_tuple_used("security-tool", 0, 2, "basic")
        assert used is True

    @pytest.mark.asyncio
    async def test_unused_tuple_returns_false(self, db):
        """An unrecorded tuple should return False."""
        used = await db.is_tuple_used("pqc-cryptography", 5, 3, "contrarian")
        assert used is False

    @pytest.mark.asyncio
    async def test_get_unused_tuple_count(self, db):
        """Should report how many tuples remain unused for a category."""
        count = await db.get_unused_tuple_count("security-tool")
        assert count > 0  # Should have available tuples

    @pytest.mark.asyncio
    async def test_record_many_tuples(self, db):
        """Recording many tuples should work without error."""
        for i in range(50):
            await db.record_used_tuple("security-tool", i % 8, i % 5, "basic")
        # Some will be duplicates due to modular arithmetic
        used = await db.is_tuple_used("security-tool", 0, 0, "basic")
        assert used is True


class TestAutoScanWithDedup:
    @pytest.mark.asyncio
    async def test_500_ideas_85_percent_unique(self, db):
        """At 500 ideas, uniqueness must stay above 85%."""
        ideas = await run_auto_scan(db, count=500)
        names = [i.name for i in ideas]
        unique_pct = len(set(names)) / len(names) if names else 0
        assert unique_pct >= 0.85, f"Uniqueness only {unique_pct:.1%} at 500 ideas"

    @pytest.mark.asyncio
    async def test_second_run_no_duplicates(self, db):
        """A second auto-scan should not reproduce names from the first."""
        first = await run_auto_scan(db, count=50)
        first_names = {i.name for i in first}
        second = await run_auto_scan(db, count=50)
        second_names = [i.name for i in second]
        collisions = [n for n in second_names if n in first_names]
        collision_pct = len(collisions) / len(second_names) if second_names else 0
        assert collision_pct < 0.15, f"{len(collisions)} collisions ({collision_pct:.0%}) between runs"

    @pytest.mark.asyncio
    async def test_auto_scan_records_tuples(self, db):
        """Auto-scan should record used tuples in the DB."""
        await run_auto_scan(db, count=10)
        # Check that some tuples were recorded
        cursor = await db.db.execute("SELECT COUNT(*) FROM used_tuples")
        row = await cursor.fetchone()
        assert row[0] >= 10


# === Issue #2: SQL-optimized queries ===


class TestSQLQueries:
    @pytest.mark.asyncio
    async def test_count_by_category_sql(self, db):
        """count_ideas_by_category should use SQL GROUP BY, not Python."""
        for i, cat in enumerate(list(IdeaCategory)[:4]):
            for j in range(3):
                idea = Idea(
                    name=f"SQL Test {i}-{j}",
                    tagline="T",
                    description="D",
                    category=cat,
                    market_analysis="M",
                    feasibility_score=0.7,
                    mvp_scope="MVP",
                )
                await db.save_idea(idea)
        counts = await db.count_ideas_by_category()
        assert isinstance(counts, dict)
        assert len(counts) >= 4
        assert all(isinstance(v, int) for v in counts.values())

    @pytest.mark.asyncio
    async def test_search_ideas_sql(self, db):
        """search_ideas should use SQL LIKE, not Python filtering."""
        idea = Idea(
            name="Quantum CRL Optimizer",
            tagline="Optimize CRLs for PQC",
            description="Handles large PQC CRL signatures",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
            market_analysis="Big market",
            feasibility_score=0.85,
            mvp_scope="CLI tool",
        )
        await db.save_idea(idea)
        results = await db.search_ideas("quantum")
        assert len(results) >= 1
        assert results[0].name == "Quantum CRL Optimizer"

    @pytest.mark.asyncio
    async def test_search_empty_returns_empty(self, db):
        """Searching for nonexistent term returns empty list."""
        results = await db.search_ideas("xyznonexistent")
        assert results == []

    @pytest.mark.asyncio
    async def test_get_all_idea_names(self, db):
        """get_all_idea_names returns names without loading full objects."""
        taglines = [
            "automate container deployment pipelines",
            "scan kubernetes manifests for drift",
            "generate terraform modules from diagrams",
            "batch process webhook retry queues",
            "orchestrate multi-cloud DNS failover",
        ]
        for i in range(5):
            idea = Idea(
                name=f"Name Only {i}",
                tagline=taglines[i],
                description="D",
                category=IdeaCategory.AUTOMATION,
                market_analysis="M",
                feasibility_score=0.7,
                mvp_scope="MVP",
            )
            await db.save_idea(idea)
        names = await db.get_all_idea_names()
        assert len(names) == 5
        assert all(isinstance(n, str) for n in names)


# === Issue #3: WAL mode and busy_timeout ===


class TestDatabaseHardening:
    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self, db):
        """Database should use WAL journal mode."""
        cursor = await db.db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
        assert row[0] == "wal"

    @pytest.mark.asyncio
    async def test_busy_timeout_set(self, db):
        """Database should have busy_timeout > 0."""
        cursor = await db.db.execute("PRAGMA busy_timeout")
        row = await cursor.fetchone()
        assert row[0] >= 5000
