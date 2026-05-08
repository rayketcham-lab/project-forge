"""TDD: vertical drill-down UI on /explore + dashboard.

The user can now: visit /explore?vertical=government, see ONLY ideas that
match that vertical, and use a chip row above the existing category chips
to switch industries. Dashboard gets a "Browse by Industry" panel.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory


def _idea(name: str, *, desc: str = "d", category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
          score: float = 0.85) -> Idea:
    return Idea(
        name=name,
        tagline=f"tag for {name}",
        description=desc,
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def client(tmp_path):
    """App + seeded DB with one idea per major vertical."""
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "ui.db"
    await db.connect()

    seed = [
        _idea("Federal SBOM Tool", desc="A FedRAMP-aware SBOM scanner for federal agencies."),
        _idea("HIPAA EHR Audit", desc="HIPAA-compliant patient EHR audit logger for hospitals."),
        _idea("FERPA Records Tool", desc="K-12 student records FERPA compliance tool."),
        _idea("PCI-DSS Audit Bot", desc="PCI-DSS audit automation for fintech payment processors."),
        _idea("Generic CLI", desc="A horizontal CLI tool with no industry focus."),
    ]
    for idea in seed:
        await db.save_idea(idea)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ── /explore?vertical=... filters correctly ──────────────────────────


class TestExploreVerticalFilter:
    @pytest.mark.asyncio
    async def test_government_filter_shows_only_government_ideas(self, client):
        resp = await client.get("/explore?vertical=government")

        assert resp.status_code == 200
        html = resp.text
        assert "Federal SBOM Tool" in html
        assert "HIPAA EHR Audit" not in html
        assert "FERPA Records Tool" not in html
        assert "Generic CLI" not in html

    @pytest.mark.asyncio
    async def test_healthcare_filter_shows_only_healthcare(self, client):
        resp = await client.get("/explore?vertical=healthcare")
        assert "HIPAA EHR Audit" in resp.text
        assert "Federal SBOM Tool" not in resp.text

    @pytest.mark.asyncio
    async def test_no_vertical_filter_shows_all(self, client):
        resp = await client.get("/explore")
        assert "Federal SBOM Tool" in resp.text
        assert "HIPAA EHR Audit" in resp.text
        assert "Generic CLI" in resp.text

    @pytest.mark.asyncio
    async def test_unknown_vertical_returns_empty_or_400(self, client):
        resp = await client.get("/explore?vertical=not-real")
        assert resp.status_code in (200, 400)
        if resp.status_code == 200:
            assert "No ideas found" in resp.text or "Federal SBOM Tool" not in resp.text


# ── /explore renders the vertical chip row ───────────────────────────


class TestExploreVerticalChipRow:
    @pytest.mark.asyncio
    async def test_explore_renders_vertical_chips(self, client):
        resp = await client.get("/explore")
        html = resp.text
        for slug in ("government", "healthcare", "education", "finance",
                     "retail", "hospitality", "manufacturing", "energy", "telco"):
            assert f"vertical={slug}" in html, f"Missing chip link for {slug}"

    @pytest.mark.asyncio
    async def test_active_vertical_chip_marked(self, client):
        resp = await client.get("/explore?vertical=government")
        assert "chip-active" in resp.text


# ── Dashboard renders a "Browse by Industry" panel ───────────────────


class TestDashboardIndustryPanel:
    @pytest.mark.asyncio
    async def test_dashboard_includes_browse_by_industry(self, client):
        resp = await client.get("/")
        html = resp.text
        assert "Browse by Industry" in html or "By Industry" in html
        for slug in ("government", "healthcare", "finance"):
            assert f"vertical={slug}" in html
