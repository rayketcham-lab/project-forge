"""Tests for URL ingestion UI on the dashboard."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_url_ui.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


class TestDashboardUrlIngestSection:
    """The dashboard should have a visible URL input form."""

    @pytest.mark.asyncio
    async def test_dashboard_has_url_input(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        html = resp.text
        assert 'id="url-input"' in html

    @pytest.mark.asyncio
    async def test_dashboard_has_url_submit_button(self, client):
        resp = await client.get("/")
        html = resp.text
        assert 'id="url-submit-btn"' in html

    @pytest.mark.asyncio
    async def test_dashboard_has_url_section_heading(self, client):
        """The URL ingest pane is the default of the ingest tab strip.
        The 'Add Idea from URL' heading was replaced by a tabbed UI
        ('From URL' / 'Quick Text' / '5-Phase Wizard') in commit fc4ccb9
        when the 5-phase wizard shipped — the tab label is the new heading."""
        resp = await client.get("/")
        html = resp.text
        assert "From URL" in html

    @pytest.mark.asyncio
    async def test_dashboard_has_category_select(self, client):
        """Optional category hint dropdown."""
        resp = await client.get("/")
        html = resp.text
        assert 'id="url-category"' in html


class TestUrlIngestJavaScript:
    """The app.js should have the submitUrl function."""

    @pytest.mark.asyncio
    async def test_app_js_has_submit_url_function(self, client):
        resp = await client.get("/static/app.js")
        assert resp.status_code == 200
        assert "submitUrl" in resp.text

    @pytest.mark.asyncio
    async def test_app_js_calls_from_url_endpoint(self, client):
        resp = await client.get("/static/app.js")
        assert "/api/ideas/from-url" in resp.text
