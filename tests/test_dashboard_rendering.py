"""Tests for dashboard rendering — correct tab content, stats, dedup."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.cron.auto_scan import run_auto_scan
from project_forge.engine.super_ideas import SuperIdeaGenerator
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_render.db"
    await db.connect()
    await run_auto_scan(db, count=30)
    gen = SuperIdeaGenerator(db)
    await gen.generate(count=3)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


class TestIdeasTabContent:
    """The Ideas tab must show regular (non-super) ideas."""

    @pytest.mark.asyncio
    async def test_ideas_tab_has_regular_ideas(self, client):
        resp = await client.get("/")
        html = resp.text
        # Find content between tab-ideas and tab-super-ideas
        ideas_section = html.split('id="tab-ideas"')[1].split('id="tab-super-ideas"')[0]
        assert "idea-card" in ideas_section
        # Should NOT have [SUPER] in the ideas tab
        assert "[SUPER]" not in ideas_section

    @pytest.mark.asyncio
    async def test_ideas_tab_is_active_by_default(self, client):
        resp = await client.get("/")
        html = resp.text
        # tab-ideas should have class="tab-panel active"
        assert 'id="tab-ideas" class="tab-panel active"' in html

    @pytest.mark.asyncio
    async def test_ideas_tab_button_is_active_by_default(self, client):
        resp = await client.get("/")
        html = resp.text
        # The Ideas tab button should have class active
        assert 'class="tab-btn active" data-tab="ideas"' in html


class TestSuperIdeasTabContent:
    """The Super Ideas tab must show only [SUPER] ideas."""

    @pytest.mark.asyncio
    async def test_super_tab_has_super_ideas(self, client):
        resp = await client.get("/")
        html = resp.text
        super_section = html.split('id="tab-super-ideas"')[1].split('id="tab-add-idea"')[0]
        assert "[SUPER]" in super_section

    @pytest.mark.asyncio
    async def test_super_tab_is_not_active_by_default(self, client):
        resp = await client.get("/")
        # Super ideas panel should NOT be active
        assert 'id="tab-super-ideas" class="tab-panel active"' not in resp.text


class TestSuperIdeaDedup:
    """Super ideas on the dashboard should not have duplicates."""

    @pytest.mark.asyncio
    async def test_no_duplicate_super_ideas_in_db_query(self, client):
        supers = await db.list_super_ideas(limit=10)
        names = [s.name for s in supers]
        assert len(names) == len(set(names)), f"Duplicate super ideas: {names}"


class TestStatsDisplay:
    """Stats should display correctly and JS should update correct indices."""

    @pytest.mark.asyncio
    async def test_super_ideas_stat_shows_count_not_score(self, client):
        resp = await client.get("/")
        html = resp.text
        # Find the super ideas stat card
        stat_section = html.split("Super Ideas")[0]
        # The number right before "Super Ideas" label should be an integer, not 0.75
        lines = stat_section.strip().split("\n")
        number_line = [line for line in lines if "stat-number" in line][-1]
        # Extract the number
        import re
        match = re.search(r">(\d+)<", number_line)
        assert match, f"Super Ideas stat should be an integer, got: {number_line}"
        count = int(match.group(1))
        assert count >= 1

    @pytest.mark.asyncio
    async def test_js_cache_bust_parameter(self, client):
        """Static assets should have cache-busting to prevent stale JS."""
        resp = await client.get("/")
        html = resp.text
        assert "app.js?v=" in html
