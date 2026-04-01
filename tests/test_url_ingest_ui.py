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
        resp = await client.get("/")
        html = resp.text
        assert "Add Idea from URL" in html

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
