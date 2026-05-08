"""TDD: Fix super ideas not showing on dashboard when they exist in DB.

Bug: Dashboard fetches top 20 recent ideas and filters for [SUPER] prefix,
but if super ideas are older than the 20 most recent, they never appear.
Stats count says 15 but content shows "No super ideas yet."

Also: app.js auto-refresh updates wrong stat indices (skips super_ideas count).
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def seeded_client(tmp_path):
    """Seed DB with super ideas that are OLDER than regular ideas."""
    db.db_path = tmp_path / "test_super_bug.db"
    await db.connect()

    # Insert 3 super ideas with old timestamps
    sql = (
        "INSERT INTO ideas (id, name, tagline, description, category, market_analysis, "
        "feasibility_score, mvp_scope, tech_stack, generated_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    )
    for i in range(3):
        await db.db.execute(
            sql,
            (
                f"super-{i}",
                f"[SUPER] Mega Project {i}",
                f"Super tagline {i}",
                f"Super description {i}",
                "security-tool",
                "N/A",
                0.9,
                "N/A",
                "[]",
                "2025-01-01T00:00:00",
                "new",
            ),
        )

    # Insert 25 regular ideas with RECENT timestamps (push supers out of top 20)
    for i in range(25):
        await db.db.execute(
            sql,
            (
                f"regular-{i}",
                f"Regular Idea {i}",
                f"Tagline {i}",
                f"Description {i}",
                "security-tool",
                "N/A",
                0.5,
                "N/A",
                "[]",
                "2026-04-01T00:00:00",
                "new",
            ),
        )
    await db.db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


class TestSuperIdeasDashboardBug:
    """Super ideas must appear on dashboard even when they're older than recent ideas."""

    @pytest.mark.asyncio
    async def test_super_ideas_appear_when_older_than_top_20(self, seeded_client):
        """Core bug: super ideas outside the top-20 window must still render."""
        resp = await seeded_client.get("/")
        assert resp.status_code == 200
        assert "[SUPER]" in resp.text
        assert "No super ideas yet" not in resp.text

    @pytest.mark.asyncio
    async def test_super_ideas_count_matches_display(self, seeded_client):
        """Stats count and displayed super ideas must be consistent."""
        resp = await seeded_client.get("/")
        html = resp.text
        # Stats card shows 3 super ideas
        assert ">3<" in html  # stat-number for super ideas
        # And the tab panel actually renders them
        assert "Mega Project" in html

    @pytest.mark.asyncio
    async def test_dashboard_db_has_dedicated_super_query(self, seeded_client):
        """The DB layer should have a method to list super ideas directly."""
        # This tests that we added a proper query instead of filtering in Python
        assert hasattr(db, "list_super_ideas"), "Database should have list_super_ideas() method"
        supers = await db.list_super_ideas(limit=6)
        assert len(supers) == 3
        assert all(s.name.startswith("[SUPER]") for s in supers)


class TestSuperIdeasDisplayLimit:
    """Dashboard must show ALL active super ideas — not just the first 6."""

    @pytest_asyncio.fixture
    async def client_seven_supers(self, tmp_path):
        db.db_path = tmp_path / "test_super_limit.db"
        await db.connect()
        sql = (
            "INSERT INTO ideas (id, name, tagline, description, category, market_analysis, "
            "feasibility_score, mvp_scope, tech_stack, generated_at, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        )
        for i in range(7):
            await db.db.execute(
                sql,
                (
                    f"super-limit-{i}",
                    f"[SUPER] Cluster Project {i}",
                    f"Super tagline {i}: domain",
                    f"Super description {i}",
                    "security-tool",
                    "N/A",
                    0.9,
                    "N/A",
                    "[]",
                    f"2025-01-0{i + 1}T00:00:00",
                    "new",
                ),
            )
        await db.db.commit()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await db.close()

    @pytest.mark.asyncio
    async def test_all_seven_super_ideas_rendered(self, client_seven_supers):
        """When 7 active super ideas exist, all 7 must appear in the dashboard.

        Regression: list_super_ideas(limit=6) in the route handler silently
        truncated the display list while stats.super_ideas correctly reported 7.
        """
        resp = await client_seven_supers.get("/")
        assert resp.status_code == 200
        rendered = sum(f"Cluster Project {i}" in resp.text for i in range(7))
        assert rendered == 7, (
            f"Expected 7 super idea cards, got {rendered}. "
            "Route handler likely has list_super_ideas(limit=6) — remove the cap."
        )

    @pytest.mark.asyncio
    async def test_stat_count_matches_display_count(self, client_seven_supers):
        """stats.super_ideas (stat card) must equal the number of rendered cards."""
        resp = await client_seven_supers.get("/")
        assert ">7<" in resp.text, "Stat card must show 7 super ideas"
        rendered = sum(f"Cluster Project {i}" in resp.text for i in range(7))
        assert rendered == 7, f"Dashboard tab must render all 7, got {rendered}"


class TestStatsAutoRefreshIndices:
    """app.js auto-refresh must update all 5 stat cards with correct values."""

    @pytest.mark.asyncio
    async def test_js_updates_super_ideas_stat(self, seeded_client):
        """app.js must update the super_ideas stat card (index 3)."""
        resp = await seeded_client.get("/static/app.js")
        js = resp.text
        # Must reference stats.super_ideas
        assert "stats.super_ideas" in js

    @pytest.mark.asyncio
    async def test_js_updates_all_five_stats(self, seeded_client):
        """app.js must update all 5 stat cards, not just 4."""
        resp = await seeded_client.get("/static/app.js")
        js = resp.text
        # Should reference index 4 (avg score is the 5th card)
        assert "numbers[4]" in js
