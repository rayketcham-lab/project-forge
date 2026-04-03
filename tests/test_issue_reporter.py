"""Tests for the issue reporter feature (issue #18)."""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_issue_reporter.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_issue_missing_description_returns_422(client):
    """POST without description must fail validation."""
    resp = await client.post(
        "/api/issues/report",
        json={"issue_type": "ui_bug"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_report_issue_invalid_type_returns_422(client):
    """POST with an invalid issue_type must fail validation."""
    resp = await client.post(
        "/api/issues/report",
        json={"issue_type": "not_real", "description": "Something is broken"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_report_issue_description_too_short_returns_422(client):
    """Description under 5 chars must fail validation."""
    resp = await client.post(
        "/api/issues/report",
        json={"issue_type": "ui_bug", "description": "Hi"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_report_issue_invalid_severity_returns_422(client):
    """Invalid severity value must fail validation."""
    resp = await client.post(
        "/api/issues/report",
        json={"issue_type": "ui_bug", "description": "A real bug here", "severity": "extreme"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Successful submission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_issue_success(client):
    """Valid issue report should return success with issue URL."""
    with patch(
        "project_forge.web.routes.create_gh_issue",
        new_callable=AsyncMock,
        return_value="https://github.com/rayketcham-lab/project-forge/issues/99",
    ):
        resp = await client.post(
            "/api/issues/report",
            json={
                "issue_type": "ui_bug",
                "description": "The approve button does not work",
                "page_url": "/ideas/abc123",
                "severity": "high",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert "issue_url" in data
    assert data["issue_url"].startswith("https://github.com/")


@pytest.mark.asyncio
async def test_report_issue_gh_failure_returns_success_false(client):
    """When GitHub issue creation fails, response should indicate failure."""
    with patch(
        "project_forge.web.routes.create_gh_issue",
        new_callable=AsyncMock,
        return_value=None,
    ):
        resp = await client.post(
            "/api/issues/report",
            json={
                "issue_type": "feature_request",
                "description": "I want a dark mode toggle",
                "severity": "low",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "error" in data


@pytest.mark.asyncio
async def test_report_issue_all_fields(client):
    """Submit with all optional fields populated."""
    with patch(
        "project_forge.web.routes.create_gh_issue",
        new_callable=AsyncMock,
        return_value="https://github.com/rayketcham-lab/project-forge/issues/100",
    ):
        resp = await client.post(
            "/api/issues/report",
            json={
                "issue_type": "wrong_data",
                "description": "The feasibility score seems miscalculated for this idea",
                "page_url": "/ideas/da03d1d1cdab",
                "page_context": "idea_detail",
                "expected_behavior": "Score should be higher given the tech stack",
                "severity": "medium",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_triggers_after_max_requests(client):
    """Submitting more than 5 issues in 60s should trigger 429."""
    with patch(
        "project_forge.web.routes.create_gh_issue",
        new_callable=AsyncMock,
        return_value="https://github.com/rayketcham-lab/project-forge/issues/99",
    ):
        # Clear rate limiter state
        import project_forge.web.routes as routes_mod

        routes_mod._rate_limit_store.clear()

        for i in range(5):
            resp = await client.post(
                "/api/issues/report",
                json={"issue_type": "ui_bug", "description": f"Rate limit test issue {i}"},
            )
            assert resp.status_code == 200

        # 6th request should be rate limited
        resp = await client.post(
            "/api/issues/report",
            json={"issue_type": "ui_bug", "description": "This should be rate limited"},
        )
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Issue types endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_issue_types(client):
    """GET /api/issues/types should return available issue types."""
    resp = await client.get("/api/issues/types")
    assert resp.status_code == 200
    types = resp.json()
    assert isinstance(types, list)
    assert len(types) >= 5
    ids = [t["id"] for t in types]
    assert "ui_bug" in ids
    assert "feature_request" in ids
    for t in types:
        assert "id" in t
        assert "label" in t
        assert "description" in t


# ---------------------------------------------------------------------------
# UI presence tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_floating_button_present_on_dashboard(client):
    """Dashboard page must include the issue reporter floating button."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'id="issue-reporter-fab"' in resp.text


@pytest.mark.asyncio
async def test_floating_button_present_on_idea_detail(client):
    """Idea detail page must include the issue reporter floating button."""
    from project_forge.models import Idea, IdeaCategory

    idea = Idea(
        name="FAB Test",
        tagline="Tag",
        description="Desc",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Market",
        feasibility_score=0.7,
        mvp_scope="MVP",
        tech_stack=["python"],
    )
    await db.save_idea(idea)
    resp = await client.get(f"/ideas/{idea.id}")
    assert resp.status_code == 200
    assert 'id="issue-reporter-fab"' in resp.text


# ---------------------------------------------------------------------------
# Fallback structuring tests
# ---------------------------------------------------------------------------


def test_fallback_issue_structure():
    """Fallback issue creation should produce title, body, labels."""
    from project_forge.web.routes import IssueReport, _fallback_issue

    report = IssueReport(
        issue_type="ui_bug",
        description="The approve button is invisible on mobile screens",
        page_url="/ideas/abc123",
        severity="high",
    )
    result = _fallback_issue(report)
    assert "title" in result
    assert "body" in result
    assert "labels" in result
    assert isinstance(result["labels"], list)
    assert "bug" in result["labels"]
    assert report.description in result["body"]


def test_fallback_issue_critical_severity_adds_label():
    """Critical severity should add 'critical' label."""
    from project_forge.web.routes import IssueReport, _fallback_issue

    report = IssueReport(
        issue_type="ui_bug",
        description="The entire dashboard is blank — nothing renders",
        severity="critical",
    )
    result = _fallback_issue(report)
    assert "critical" in result["labels"]
