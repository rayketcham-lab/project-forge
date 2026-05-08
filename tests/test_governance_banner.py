"""Tests for governance banner — autonomous project under human authority."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.scaffold.builder import build_scaffold_spec, render_scaffold
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_banner.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


class TestGovernanceBannerOnDashboard:
    """Every page should have a governance banner in the base template."""

    @pytest.mark.asyncio
    async def test_dashboard_has_governance_banner(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert 'class="governance-banner"' in resp.text

    @pytest.mark.asyncio
    async def test_banner_mentions_human_directed(self, client):
        resp = await client.get("/")
        assert "human-directed" in resp.text.lower() or "human directed" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_banner_mentions_autonomous(self, client):
        resp = await client.get("/")
        assert "autonomous" in resp.text.lower()

    @pytest.mark.asyncio
    async def test_banner_mentions_human_oversight(self, client):
        resp = await client.get("/")
        html = resp.text.lower()
        # New contract: emphasize human-directed governance,
        # not a specific persona/profession.
        assert "human" in html
        assert "autonomous" in html

    @pytest.mark.asyncio
    async def test_explore_page_has_governance_banner(self, client):
        resp = await client.get("/explore")
        assert 'class="governance-banner"' in resp.text

    @pytest.mark.asyncio
    async def test_projects_page_has_governance_banner(self, client):
        resp = await client.get("/projects")
        assert 'class="governance-banner"' in resp.text


class TestGovernanceBannerInScaffoldREADME:
    """Every scaffolded project README should have the governance notice."""

    def test_readme_has_governance_section(self, tmp_path):
        idea = Idea(
            name="Test PKI Tool",
            tagline="A test tool",
            description="Testing governance.",
            category=IdeaCategory.PQC_CRYPTOGRAPHY,
            market_analysis="Market.",
            feasibility_score=0.8,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        spec = build_scaffold_spec(idea)
        project_dir = render_scaffold(spec, idea, tmp_path)
        readme = (project_dir / "README.md").read_text()
        assert "Autonomous Project" in readme or "Governance" in readme

    def test_readme_mentions_human_oversight(self, tmp_path):
        idea = Idea(
            name="Test Oversight Tool",
            tagline="A test tool",
            description="Testing.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Market.",
            feasibility_score=0.7,
            mvp_scope="MVP.",
            tech_stack=["rust"],
        )
        spec = build_scaffold_spec(idea)
        project_dir = render_scaffold(spec, idea, tmp_path)
        readme = (project_dir / "README.md").read_text()
        lower = readme.lower()
        assert "human" in lower
        assert "review" in lower or "oversight" in lower or "directed" in lower
