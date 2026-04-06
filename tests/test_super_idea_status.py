"""Tests for super idea status fix (issue #9).

Super ideas should NOT be auto-approved. They should be created as 'new'
like regular ideas, so the approved count only reflects manual approvals.
"""

from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.engine.super_ideas import SuperIdeaGenerator
from project_forge.storage.db import Database
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def seeded_db(tmp_path: Path):
    database = Database(tmp_path / "test_super_status.db")
    await database.connect()
    await run_auto_scan(database, count=50)
    yield database
    await database.close()


class TestSuperIdeaDefaultStatus:
    @pytest.mark.asyncio
    async def test_super_ideas_created_as_new(self, seeded_db):
        """Super ideas must be stored with status='new', not 'approved'."""
        gen = SuperIdeaGenerator(seeded_db)
        supers = await gen.generate(count=3)
        assert len(supers) >= 1

        for si in supers:
            stored = await seeded_db.get_idea(si.id)
            assert stored is not None
            assert stored.status == "new", (
                f"Super idea '{stored.name}' has status='{stored.status}', "
                "expected 'new'. Super ideas should not be auto-approved."
            )

    @pytest.mark.asyncio
    async def test_super_ideas_not_in_approved_count(self, seeded_db):
        """Approved count should be 0 when no manual approvals have happened."""
        gen = SuperIdeaGenerator(seeded_db)
        await gen.generate(count=3)

        stats = await seeded_db.get_stats()
        approved_count = stats["ideas_by_status"].get("approved", 0)
        assert approved_count == 0, (
            f"Expected 0 approved ideas (no manual approvals), got {approved_count}. Super ideas inflating the count."
        )

    @pytest.mark.asyncio
    async def test_manual_approve_still_works(self, seeded_db):
        """Manually approving a super idea should change its status."""
        gen = SuperIdeaGenerator(seeded_db)
        supers = await gen.generate(count=1)
        si = supers[0]

        # Manually approve
        await seeded_db.update_idea_status(si.id, "approved")
        stored = await seeded_db.get_idea(si.id)
        assert stored.status == "approved"

        # Now approved count should be exactly 1
        stats = await seeded_db.get_stats()
        assert stats["ideas_by_status"].get("approved", 0) == 1


class TestDashboardSuperVsRegular:
    @pytest.mark.asyncio
    async def test_stats_include_super_count(self, seeded_db):
        """Stats should report how many ideas are super vs regular."""
        gen = SuperIdeaGenerator(seeded_db)
        await gen.generate(count=3)

        stats = await seeded_db.get_stats()
        assert "super_ideas" in stats, "Stats should include 'super_ideas' count"
        assert stats["super_ideas"] >= 3

    @pytest.mark.asyncio
    async def test_stats_regular_count_excludes_supers(self, seeded_db):
        """Regular idea count should exclude super ideas."""
        # Count baseline regular ideas (dedup may reduce from the requested 50)
        baseline_stats = await seeded_db.get_stats()
        baseline_regular = baseline_stats["total_ideas"] - baseline_stats["super_ideas"]

        gen = SuperIdeaGenerator(seeded_db)
        await gen.generate(count=3)

        stats = await seeded_db.get_stats()
        total = stats["total_ideas"]
        super_count = stats["super_ideas"]
        regular_count = total - super_count
        assert regular_count == baseline_regular, f"Expected {baseline_regular} regular ideas, got {regular_count}"


class TestDashboardRendering:
    @pytest_asyncio.fixture
    async def client(self, tmp_path):
        db.db_path = tmp_path / "test_dash.db"
        await db.connect()
        await run_auto_scan(db, count=15)
        gen = SuperIdeaGenerator(db)
        await gen.generate(count=2)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await db.close()

    @pytest.mark.asyncio
    async def test_dashboard_shows_super_count(self, client):
        """Dashboard should display the super ideas count."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "Super" in resp.text or "super" in resp.text
