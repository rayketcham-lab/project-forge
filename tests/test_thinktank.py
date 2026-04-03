"""Tests for the Think Tank feature — surfaces Project Forge's own improvement ideas."""

import json
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db

# --- Fixtures ---


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_thinktank.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


SAMPLE_ISSUES = [
    {
        "number": 1,
        "title": "Add input-tuple tracking and content fingerprinting",
        "state": "CLOSED",
        "labels": [{"name": "feature"}],
        "url": "https://github.com/rayketcham-lab/project-forge/issues/1",
        "createdAt": "2026-03-31T04:56:09Z",
        "closedAt": "2026-03-31T05:30:00Z",
    },
    {
        "number": 2,
        "title": "Replace in-memory counting with SQL COUNT/GROUP BY",
        "state": "CLOSED",
        "labels": [{"name": "feature"}],
        "url": "https://github.com/rayketcham-lab/project-forge/issues/2",
        "createdAt": "2026-03-31T04:56:09Z",
        "closedAt": "2026-03-31T05:45:00Z",
    },
    {
        "number": 9,
        "title": "fix: Super ideas auto-approved, inflating approved count",
        "state": "OPEN",
        "labels": [{"name": "bug"}],
        "url": "https://github.com/rayketcham-lab/project-forge/issues/9",
        "createdAt": "2026-03-31T17:47:40Z",
        "closedAt": None,
    },
    {
        "number": 10,
        "title": "feat: URL-to-idea ingestion + resource tracking",
        "state": "OPEN",
        "labels": [{"name": "feature"}],
        "url": "https://github.com/rayketcham-lab/project-forge/issues/10",
        "createdAt": "2026-03-31T20:35:11Z",
        "closedAt": None,
    },
]


# --- Unit tests for list_self_issues ---


class TestListSelfIssues:
    """Tests for the github.list_self_issues function."""

    def test_returns_structured_issues(self):
        """list_self_issues returns a list of dicts with expected keys."""
        from project_forge.scaffold.github import list_self_issues

        mock_output = json.dumps(SAMPLE_ISSUES)
        with patch("project_forge.scaffold.github._run_gh", return_value=mock_output):
            issues = list_self_issues()

        assert len(issues) == 4
        assert issues[0]["number"] == 1
        assert issues[0]["title"] == "Add input-tuple tracking and content fingerprinting"
        assert issues[0]["state"] == "CLOSED"

    def test_separates_open_and_closed(self):
        """list_self_issues can filter by state."""
        from project_forge.scaffold.github import list_self_issues

        mock_output = json.dumps([i for i in SAMPLE_ISSUES if i["state"] == "CLOSED"])
        with patch("project_forge.scaffold.github._run_gh", return_value=mock_output):
            closed = list_self_issues(state="closed")
        assert len(closed) == 2
        assert all(i["state"] == "CLOSED" for i in closed)

    def test_empty_issues(self):
        """list_self_issues returns empty list when no issues exist."""
        from project_forge.scaffold.github import list_self_issues

        with patch("project_forge.scaffold.github._run_gh", return_value="[]"):
            issues = list_self_issues()
        assert issues == []

    def test_gh_failure_raises(self):
        """list_self_issues raises RuntimeError on gh failure."""
        from project_forge.scaffold.github import list_self_issues

        with patch("project_forge.scaffold.github._run_gh", side_effect=RuntimeError("gh failed")):
            with pytest.raises(RuntimeError, match="gh failed"):
                list_self_issues()


# --- API route tests ---


class TestThinkTankAPI:
    """Tests for the /api/thinktank endpoint."""

    @pytest.mark.asyncio
    async def test_api_returns_issues(self, client):
        """GET /api/thinktank returns open and closed issues."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/api/thinktank")
        assert resp.status_code == 200
        data = resp.json()
        assert "open" in data
        assert "closed" in data
        assert len(data["open"]) == 2
        assert len(data["closed"]) == 2

    @pytest.mark.asyncio
    async def test_api_counts(self, client):
        """GET /api/thinktank includes counts."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/api/thinktank")
        data = resp.json()
        assert data["open_count"] == 2
        assert data["closed_count"] == 2

    @pytest.mark.asyncio
    async def test_api_handles_gh_failure(self, client):
        """GET /api/thinktank returns 502 when gh CLI fails."""
        with patch(
            "project_forge.scaffold.github.list_self_issues",
            side_effect=RuntimeError("gh not found"),
        ):
            resp = await client.get("/api/thinktank")
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_api_empty(self, client):
        """GET /api/thinktank returns empty lists when no issues."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/api/thinktank")
        data = resp.json()
        assert data["open"] == []
        assert data["closed"] == []
        assert data["open_count"] == 0
        assert data["closed_count"] == 0


# --- Page route tests ---


class TestThinkTankPage:
    """Tests for the /thinktank page route."""

    @pytest.mark.asyncio
    async def test_page_renders(self, client):
        """GET /thinktank returns 200 with Think Tank content."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/thinktank")
        assert resp.status_code == 200
        assert "Think Tank" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_closed_issues(self, client):
        """GET /thinktank displays completed improvements."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/thinktank")
        assert "input-tuple tracking" in resp.text
        assert "SQL COUNT/GROUP BY" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_open_issues(self, client):
        """GET /thinktank displays open improvement ideas."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/thinktank")
        assert "Super ideas auto-approved" in resp.text
        assert "URL-to-idea ingestion" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_counts(self, client):
        """GET /thinktank displays issue counts."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=SAMPLE_ISSUES):
            resp = await client.get("/thinktank")
        # Should show "2 Done" and "2 Open" or similar counts
        assert "2" in resp.text  # At minimum the counts appear

    @pytest.mark.asyncio
    async def test_page_handles_gh_failure_gracefully(self, client):
        """GET /thinktank renders even if gh CLI fails (shows error state)."""
        with patch(
            "project_forge.scaffold.github.list_self_issues",
            side_effect=RuntimeError("gh unavailable"),
        ):
            resp = await client.get("/thinktank")
        assert resp.status_code == 200
        # Should render an error or empty state, not crash
        assert "Think Tank" in resp.text


# --- Dashboard link test ---


class TestDashboardThinkTankLink:
    """Tests that the dashboard links to the Think Tank."""

    @pytest.mark.asyncio
    async def test_dashboard_has_thinktank_tab(self, client):
        """Dashboard should have a Think Tank tab or link."""
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "thinktank" in resp.text.lower() or "think tank" in resp.text.lower()


# --- Proposal (self-improvement idea) helpers ---


def _make_self_improvement_idea(**overrides):
    """Build a self-improvement Idea without touching IdeaCategory enum at call site."""
    from project_forge.models import Idea, IdeaCategory

    defaults = {
        "name": "Add better logging",
        "tagline": "Improve observability of the forge engine",
        "description": "Structured logging with correlation IDs for every generation run.",
        "category": IdeaCategory.SELF_IMPROVEMENT,
        "market_analysis": "Internal improvement.",
        "feasibility_score": 0.85,
        "mvp_scope": "Add structlog to the engine module.",
        "tech_stack": ["python", "structlog"],
        "status": "new",
    }
    defaults.update(overrides)
    return Idea(**defaults)


# --- Proposal API tests ---


class TestThinkTankProposals:
    """Tests for self-improvement proposals in the Think Tank."""

    @pytest.mark.asyncio
    async def test_api_thinktank_includes_proposals(self, client):
        """GET /api/thinktank includes proposals key with self-improvement ideas."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea()
        await app_db.save_idea(idea)

        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/api/thinktank")

        assert resp.status_code == 200
        data = resp.json()
        assert "proposals" in data
        assert any(p["id"] == idea.id for p in data["proposals"])

    @pytest.mark.asyncio
    async def test_api_thinktank_proposal_count(self, client):
        """GET /api/thinktank returns correct proposal_count."""
        from project_forge.web.app import db as app_db

        taglines = ["add rate limiting to endpoints", "structured logging overhaul", "CI pipeline hardening"]
        for i in range(3):
            await app_db.save_idea(_make_self_improvement_idea(name=f"Proposal {i}", tagline=taglines[i]))

        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/api/thinktank")

        data = resp.json()
        assert data["proposal_count"] == 3

    @pytest.mark.asyncio
    async def test_page_shows_proposals_tab(self, client):
        """GET /thinktank renders a Proposals tab."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")

        assert resp.status_code == 200
        assert "Proposals" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_proposal_ideas(self, client):
        """GET /thinktank renders self-improvement idea names."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Add better logging")
        await app_db.save_idea(idea)

        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")

        assert "Add better logging" in resp.text

    @pytest.mark.asyncio
    async def test_promote_creates_github_issue(self, client):
        """POST /api/thinktank/{id}/promote creates a GitHub issue and updates status."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Smarter deduplication")
        await app_db.save_idea(idea)

        with patch(
            "project_forge.web.routes.create_issue",
            return_value="https://github.com/rayketcham-lab/project-forge/issues/42",
        ) as mock_create:
            resp = await client.post(f"/api/thinktank/{idea.id}/promote")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "promoted"
        assert data["issue_url"] == "https://github.com/rayketcham-lab/project-forge/issues/42"
        assert mock_create.called

        # Verify status updated in DB
        updated = await app_db.get_idea(idea.id)
        assert updated is not None
        assert updated.status == "approved"

    @pytest.mark.asyncio
    async def test_promote_nonexistent_returns_404(self, client):
        """POST /api/thinktank/{id}/promote returns 404 for unknown idea ID."""
        resp = await client.post("/api/thinktank/fake-id-does-not-exist/promote")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reject_proposal(self, client):
        """POST /api/thinktank/{id}/reject sets status to rejected."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Bad idea")
        await app_db.save_idea(idea)
        resp = await client.post(f"/api/thinktank/{idea.id}/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        updated = await app_db.get_idea(idea.id)
        assert updated.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_nonexistent_returns_404(self, client):
        """POST /api/thinktank/{id}/reject returns 404 for unknown idea ID."""
        resp = await client.post("/api/thinktank/nonexistent/reject")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_page_shows_forge_lab_section(self, client):
        """GET /thinktank has Forge Lab and Roadmap sections."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert "Forge Lab" in resp.text
        assert "Roadmap" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_rejected_tab(self, client):
        """GET /thinktank has a Rejected tab showing rejected proposals."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Rejected feature", status="rejected")
        await app_db.save_idea(idea)
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert "Rejected" in resp.text
        assert "Rejected feature" in resp.text

    @pytest.mark.asyncio
    async def test_page_shows_promoted_tab(self, client):
        """GET /thinktank has a Promoted tab showing promoted proposals."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Good feature", status="approved")
        await app_db.save_idea(idea)
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert "Promoted" in resp.text
        assert "Good feature" in resp.text

    @pytest.mark.asyncio
    async def test_api_includes_rejected_and_promoted(self, client):
        """GET /api/thinktank includes rejected and promoted counts."""
        from project_forge.web.app import db as app_db

        await app_db.save_idea(
            _make_self_improvement_idea(name="P1", status="approved", tagline="rate limiting for API")
        )
        await app_db.save_idea(
            _make_self_improvement_idea(name="P2", status="approved", tagline="structured logging overhaul")
        )
        await app_db.save_idea(
            _make_self_improvement_idea(name="R1", status="rejected", tagline="CI pipeline hardening")
        )
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/api/thinktank")
        data = resp.json()
        assert data["promoted_count"] == 2
        assert data["rejected_count"] == 1


# --- Promote → ci-queue label tests (issue #19) ---


class TestPromoteCIQueue:
    """Promoting a self-improvement proposal must create an issue with ci-queue label."""

    @pytest.mark.asyncio
    async def test_promote_passes_ci_queue_label(self, client):
        """Promote must call create_issue with labels=['ci-queue']."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="CI label test")
        await app_db.save_idea(idea)

        with patch(
            "project_forge.web.routes.create_issue",
            return_value="https://github.com/rayketcham-lab/project-forge/issues/50",
        ) as mock_create:
            resp = await client.post(f"/api/thinktank/{idea.id}/promote")

        assert resp.status_code == 200
        # Verify create_issue was called with the ci-queue label
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        # create_issue(repo, title, body, labels=["ci-queue"])
        if call_kwargs.kwargs.get("labels"):
            assert "ci-queue" in call_kwargs.kwargs["labels"]
        else:
            # positional args: repo, title, body, labels
            assert len(call_kwargs.args) >= 4
            assert "ci-queue" in call_kwargs.args[3]

    @pytest.mark.asyncio
    async def test_promote_issue_title_has_think_tank_prefix(self, client):
        """Promoted issue title should still have [Think Tank] prefix."""
        from project_forge.web.app import db as app_db

        idea = _make_self_improvement_idea(name="Title prefix test")
        await app_db.save_idea(idea)

        with patch(
            "project_forge.web.routes.create_issue",
            return_value="https://github.com/rayketcham-lab/project-forge/issues/51",
        ) as mock_create:
            await client.post(f"/api/thinktank/{idea.id}/promote")

        title_arg = mock_create.call_args.args[1]
        assert title_arg.startswith("[Think Tank]")


class TestCIQueueWorkflow:
    """CI workflow must include a self-improvement-queue job."""

    def test_ci_yaml_has_queue_check_job(self):
        """ci.yml must contain a self-improvement-queue job."""
        from pathlib import Path

        import yaml

        ci_path = Path("/opt/vmdata/project-forge/.github/workflows/ci.yml")
        ci = yaml.safe_load(ci_path.read_text())
        assert "self-improvement-queue" in ci["jobs"], (
            "CI workflow missing 'self-improvement-queue' job"
        )

    def test_ci_queue_job_checks_ci_queue_label(self):
        """The queue check job must query for the ci-queue label."""
        from pathlib import Path

        ci_text = Path("/opt/vmdata/project-forge/.github/workflows/ci.yml").read_text()
        assert "ci-queue" in ci_text, "CI workflow must reference 'ci-queue' label"

    def test_ci_queue_job_fails_on_open_issues(self):
        """The queue check job must exit non-zero when open ci-queue issues exist."""
        from pathlib import Path

        ci_text = Path("/opt/vmdata/project-forge/.github/workflows/ci.yml").read_text()
        # Should contain logic that fails when count > 0
        assert "exit 1" in ci_text or "::error" in ci_text, (
            "CI queue job must fail when open ci-queue issues exist"
        )
