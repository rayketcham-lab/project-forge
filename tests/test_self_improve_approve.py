"""Tests for self-improvement idea approve flow.

Bug: Approving a self-improvement idea from the detail page just flips DB status
but does NOT create a GitHub issue with ci-queue label. It should auto-promote.
Also: approved self-improvement ideas should never show the scaffold form.
"""

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_si_approve.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


def _make_si_idea(**overrides) -> Idea:
    """Create a self-improvement idea with sensible defaults."""
    defaults = dict(
        name="Rate Limit API",
        tagline="Add per-IP rate limiting",
        description="The API needs rate limiting to prevent abuse.",
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal improvement for project reliability.",
        feasibility_score=0.85,
        mvp_scope="Add slowapi middleware to FastAPI app.",
        tech_stack=["python", "fastapi"],
        status="new",
    )
    defaults.update(overrides)
    return Idea(**defaults)


def _make_regular_idea(**overrides) -> Idea:
    """Create a non-self-improvement idea."""
    defaults = dict(
        name="PKI Scanner",
        tagline="Scan for certificate issues",
        description="Tool to scan PKI infrastructure.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Growing need for PKI auditing.",
        feasibility_score=0.75,
        mvp_scope="CLI tool that checks cert chains.",
        tech_stack=["python"],
        status="new",
    )
    defaults.update(overrides)
    return Idea(**defaults)


# ---------------------------------------------------------------------------
# 1. Approve self-improvement → auto-promote to GH issue with ci-queue
# ---------------------------------------------------------------------------


class TestApproveSelfImprovementCreatesIssue:
    """POST /ideas/{id}/approve for self-improvement ideas must create a GitHub issue."""

    @pytest.mark.asyncio
    async def test_approve_creates_github_issue(self, client):
        """Approving a self-improvement idea should create a GH issue with ci-queue label."""
        idea = _make_si_idea()
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/project-forge/issues/99"
            resp = await client.post(f"/ideas/{idea.id}/approve")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        # Must have created a GitHub issue
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        # Should include ci-queue label
        labels_arg = call_kwargs.kwargs.get("labels") or call_kwargs[1].get("labels")
        if labels_arg is None:
            # Positional args: (repo, title, body, labels=...)
            # Check all args for the labels list
            for arg in call_kwargs[0]:
                if isinstance(arg, list) and "ci-queue" in arg:
                    labels_arg = arg
                    break
            if labels_arg is None:
                labels_arg = call_kwargs.kwargs.get("labels", [])
        assert "ci-queue" in labels_arg

    @pytest.mark.asyncio
    async def test_approve_stores_issue_url(self, client):
        """After promote, the idea should have the github_issue_url set."""
        idea = _make_si_idea()
        await db.save_idea(idea)

        issue_url = "https://github.com/rayketcham-lab/project-forge/issues/99"
        with patch("project_forge.web.routes.create_issue", return_value=issue_url):
            await client.post(f"/ideas/{idea.id}/approve")

        updated = await db.get_idea(idea.id)
        assert updated.github_issue_url == issue_url

    @pytest.mark.asyncio
    async def test_approve_returns_issue_url(self, client):
        """The approve response should include the issue URL."""
        idea = _make_si_idea()
        await db.save_idea(idea)

        issue_url = "https://github.com/rayketcham-lab/project-forge/issues/99"
        with patch("project_forge.web.routes.create_issue", return_value=issue_url):
            resp = await client.post(f"/ideas/{idea.id}/approve")

        data = resp.json()
        assert data.get("issue_url") == issue_url


# ---------------------------------------------------------------------------
# 2. Regular ideas: approve still works normally (no GH issue)
# ---------------------------------------------------------------------------


class TestApproveRegularIdeaUnchanged:
    """POST /ideas/{id}/approve for non-self-improvement ideas keeps existing behavior."""

    @pytest.mark.asyncio
    async def test_regular_approve_does_not_create_issue(self, client):
        """Approving a security-tool idea should NOT create a GitHub issue."""
        idea = _make_regular_idea()
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue") as mock_create:
            resp = await client.post(f"/ideas/{idea.id}/approve")

        assert resp.status_code == 200
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_regular_approve_just_changes_status(self, client):
        """Regular approve should only flip status to approved."""
        idea = _make_regular_idea()
        await db.save_idea(idea)

        resp = await client.post(f"/ideas/{idea.id}/approve")
        data = resp.json()
        assert data["status"] == "approved"


# ---------------------------------------------------------------------------
# 3. Template: no scaffold form for self-improvement ideas
# ---------------------------------------------------------------------------


class TestNoScaffoldForSelfImprovement:
    """The idea detail page should not show scaffold/repo creation for self-improvement."""

    @pytest.mark.asyncio
    async def test_approved_si_idea_has_no_scaffold_form(self, client):
        """Approved self-improvement ideas should NOT show 'Create on GitHub' scaffold button."""
        idea = _make_si_idea(status="approved")
        await db.save_idea(idea)

        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        html = resp.text
        # Must NOT contain the scaffold form
        assert "scaffold-form" not in html
        assert "Create on GitHub" not in html

    @pytest.mark.asyncio
    async def test_approved_si_idea_shows_issue_link(self, client):
        """Approved self-improvement ideas with a GH issue URL should show a link to it."""
        idea = _make_si_idea(
            status="approved",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/99",
        )
        await db.save_idea(idea)

        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        html = resp.text
        assert "issues/99" in html

    @pytest.mark.asyncio
    async def test_approved_regular_idea_still_shows_scaffold(self, client):
        """Regular approved ideas should still show the scaffold form."""
        idea = _make_regular_idea(status="approved")
        await db.save_idea(idea)

        resp = await client.get(f"/ideas/{idea.id}")
        assert resp.status_code == 200
        html = resp.text
        assert "Create on GitHub" in html
