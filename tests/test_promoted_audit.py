"""Tests for promoted idea audit — track implementation status of promoted ideas.

Covers:
1. PromotedIdeaAudit model — tracks implementation evidence for promoted ideas
2. audit_promoted_idea() — checks if a promoted idea was implemented
3. Bulk audit — audit all promoted ideas and produce a summary report
4. Status reconciliation — update idea status and close completed GH issues
5. Dashboard endpoint — /thinktank/audit shows implementation status
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_audit.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


def _make_idea(name: str, status: str = "approved", **kw) -> Idea:
    return Idea(
        name=name,
        tagline=kw.pop("tagline", f"tagline for {name}"),
        description=kw.pop("description", f"Description for {name}."),
        category=kw.pop("category", IdeaCategory.SELF_IMPROVEMENT),
        market_analysis=kw.pop("market_analysis", "Internal improvement."),
        feasibility_score=kw.pop("feasibility_score", 0.8),
        mvp_scope=kw.pop("mvp_scope", "Build it."),
        tech_stack=kw.pop("tech_stack", ["python"]),
        status=status,
        **kw,
    )


# ---------------------------------------------------------------------------
# 1. PromotedIdeaAudit model
# ---------------------------------------------------------------------------


class TestPromotedIdeaAuditModel:
    """PromotedIdeaAudit tracks implementation evidence."""

    def test_create_audit_result(self):
        from project_forge.models import PromotedIdeaAudit

        audit = PromotedIdeaAudit(
            idea_id="abc123",
            idea_name="Rate Limiting",
            status="implemented",
            evidence=["Rate limit middleware in routes.py", "Tests in test_rate_limit.py"],
            github_issue_number=29,
            github_issue_state="open",
        )
        assert audit.idea_id == "abc123"
        assert audit.status == "implemented"
        assert len(audit.evidence) == 2

    def test_audit_status_values(self):
        """Status must be one of: implemented, partial, not_implemented, unknown."""
        from project_forge.models import PromotedIdeaAudit

        for status in ["implemented", "partial", "not_implemented", "unknown"]:
            audit = PromotedIdeaAudit(
                idea_id="abc",
                idea_name="Test",
                status=status,
                evidence=[],
            )
            assert audit.status == status

    def test_audit_has_recommendation(self):
        """Audit can include a recommended action (close issue, needs work, etc.)."""
        from project_forge.models import PromotedIdeaAudit

        audit = PromotedIdeaAudit(
            idea_id="abc",
            idea_name="Test",
            status="implemented",
            evidence=["Found in codebase"],
            recommendation="close_issue",
        )
        assert audit.recommendation == "close_issue"

    def test_audit_defaults(self):
        from project_forge.models import PromotedIdeaAudit

        audit = PromotedIdeaAudit(
            idea_id="abc",
            idea_name="Test",
            status="unknown",
            evidence=[],
        )
        assert audit.github_issue_number is None
        assert audit.github_issue_state is None
        assert audit.recommendation is None


# ---------------------------------------------------------------------------
# 2. audit_promoted_idea() — check implementation evidence
# ---------------------------------------------------------------------------


class TestAuditPromotedIdea:
    """audit_promoted_idea searches the codebase for implementation evidence."""

    def test_dedup_improvements_detected_as_implemented(self):
        """Dedup improvements (#28) — dedup.py has fuzzy matching, should be detected."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea(
            "Idea Deduplication Algorithm Improvements",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/28",
        )
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.status == "implemented"
        assert len(result.evidence) > 0
        assert result.github_issue_number == 28

    def test_rate_limiting_detected_as_implemented(self):
        """Rate limiting (#29) — rate limit code in routes.py should be detected."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea(
            "Missing Rate Limiting On Hub",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/29",
        )
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.status == "implemented"
        assert any("rate" in e.lower() for e in result.evidence)

    def test_structured_logging_detected_as_not_implemented(self):
        """Structured logging (#35) — no structlog in codebase, should be not_implemented."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea(
            "Observability Additions — Structured Suite",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/35",
        )
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.status in ("not_implemented", "partial")

    def test_security_hardening_detected_as_implemented(self):
        """Security hardening (#31) — CSP, input validation should be detected."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea(
            "Security Hardening Of Api for Test",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/31",
        )
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.status == "implemented"

    def test_idea_without_github_url(self):
        """Ideas without a github_issue_url should still be auditable."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea("Dashboard Ux Improvements")
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.github_issue_number is None
        assert result.status in ("implemented", "partial", "not_implemented", "unknown")

    def test_recommendation_close_for_implemented(self):
        """Implemented ideas with open issues should recommend close_issue."""
        from project_forge.engine.audit import audit_promoted_idea

        idea = _make_idea(
            "Missing Rate Limiting On Hub",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/29",
        )
        result = audit_promoted_idea(idea, project_root=PROJECT_ROOT)
        assert result.status == "implemented"
        assert result.recommendation == "close_issue"


# ---------------------------------------------------------------------------
# 3. Bulk audit — all promoted ideas
# ---------------------------------------------------------------------------


class TestBulkAudit:
    """run_promoted_audit audits all promoted/approved ideas."""

    @pytest.mark.asyncio
    async def test_bulk_audit_returns_list(self, client):
        """run_promoted_audit should return a list of PromotedIdeaAudit."""
        from project_forge.engine.audit import run_promoted_audit

        idea1 = _make_idea("Test Idea One", status="approved")
        idea2 = _make_idea("Test Idea Two", status="approved")
        await db.save_idea(idea1)
        await db.save_idea(idea2)

        results = await run_promoted_audit(db, project_root=PROJECT_ROOT)
        assert isinstance(results, list)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_bulk_audit_skips_new_ideas(self, client):
        """Only audit promoted/approved/scaffolded/contributed ideas."""
        from project_forge.engine.audit import run_promoted_audit

        new_idea = _make_idea("New Idea", status="new")
        approved = _make_idea("Approved Idea", status="approved")
        await db.save_idea(new_idea)
        await db.save_idea(approved)

        results = await run_promoted_audit(db, project_root=PROJECT_ROOT)
        assert len(results) == 1
        assert results[0].idea_name == "Approved Idea"

    @pytest.mark.asyncio
    async def test_bulk_audit_summary(self, client):
        """Audit summary should count by status."""
        from project_forge.engine.audit import audit_summary
        from project_forge.models import PromotedIdeaAudit

        audits = [
            PromotedIdeaAudit(idea_id="a", idea_name="A", status="implemented", evidence=["x"]),
            PromotedIdeaAudit(idea_id="b", idea_name="B", status="implemented", evidence=["y"]),
            PromotedIdeaAudit(idea_id="c", idea_name="C", status="not_implemented", evidence=[]),
            PromotedIdeaAudit(idea_id="d", idea_name="D", status="partial", evidence=["z"]),
        ]
        summary = audit_summary(audits)
        assert summary["total"] == 4
        assert summary["implemented"] == 2
        assert summary["not_implemented"] == 1
        assert summary["partial"] == 1


# ---------------------------------------------------------------------------
# 4. Status reconciliation — update status, close issues
# ---------------------------------------------------------------------------


class TestStatusReconciliation:
    """reconcile_audit_results updates idea statuses and optionally closes issues."""

    @pytest.mark.asyncio
    async def test_implemented_idea_status_updated(self, client):
        """Implemented ideas should have status set to 'implemented'."""
        from project_forge.engine.audit import reconcile_audit_results
        from project_forge.models import PromotedIdeaAudit

        idea = _make_idea("Rate Limiting", status="approved")
        await db.save_idea(idea)

        audit = PromotedIdeaAudit(
            idea_id=idea.id,
            idea_name=idea.name,
            status="implemented",
            evidence=["rate limit in routes.py"],
            recommendation="close_issue",
            github_issue_number=29,
            github_issue_state="open",
        )

        await reconcile_audit_results(db, [audit], close_issues=False)
        updated = await db.get_idea(idea.id)
        assert updated.status == "implemented"

    @pytest.mark.asyncio
    async def test_not_implemented_stays_approved(self, client):
        """Not-implemented ideas should stay as approved."""
        from project_forge.engine.audit import reconcile_audit_results
        from project_forge.models import PromotedIdeaAudit

        idea = _make_idea("Structured Logging", status="approved")
        await db.save_idea(idea)

        audit = PromotedIdeaAudit(
            idea_id=idea.id,
            idea_name=idea.name,
            status="not_implemented",
            evidence=[],
        )

        await reconcile_audit_results(db, [audit], close_issues=False)
        updated = await db.get_idea(idea.id)
        assert updated.status == "approved"

    @pytest.mark.asyncio
    async def test_close_issues_flag(self, client):
        """When close_issues=True and recommendation=close_issue, should call gh."""
        from project_forge.engine.audit import reconcile_audit_results
        from project_forge.models import PromotedIdeaAudit

        idea = _make_idea(
            "Dedup",
            status="approved",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/28",
        )
        await db.save_idea(idea)

        audit = PromotedIdeaAudit(
            idea_id=idea.id,
            idea_name=idea.name,
            status="implemented",
            evidence=["dedup.py"],
            recommendation="close_issue",
            github_issue_number=28,
            github_issue_state="open",
        )

        with patch("project_forge.engine.audit.close_github_issue") as mock_close:
            await reconcile_audit_results(db, [audit], close_issues=True)
            mock_close.assert_called_once_with(repo="rayketcham-lab/project-forge", issue_number=28)


# ---------------------------------------------------------------------------
# 5. Dashboard endpoint — /thinktank/audit
# ---------------------------------------------------------------------------


class TestAuditDashboard:
    """GET /thinktank/audit shows audit results."""

    @pytest.mark.asyncio
    async def test_audit_endpoint_exists(self, client):
        resp = await client.get("/thinktank/audit")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_audit_endpoint_shows_promoted_ideas(self, client):
        """Audit page should list promoted ideas and their implementation status."""
        idea = _make_idea("Test Audit Idea", status="approved")
        await db.save_idea(idea)

        resp = await client.get("/thinktank/audit")
        assert resp.status_code == 200
        assert "Test Audit Idea" in resp.text

    @pytest.mark.asyncio
    async def test_audit_api_endpoint(self, client):
        """API endpoint returns JSON audit results."""
        idea = _make_idea("API Audit Idea", status="approved")
        await db.save_idea(idea)

        resp = await client.get("/api/thinktank/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert "audits" in data
        assert "summary" in data
        assert data["summary"]["total"] >= 1

    @pytest.mark.asyncio
    async def test_audit_api_has_summary_counts(self, client):
        """API audit response should have counts by status."""
        resp = await client.get("/api/thinktank/audit")
        data = resp.json()
        summary = data["summary"]
        assert "total" in summary
        assert "implemented" in summary
        assert "not_implemented" in summary
        assert "partial" in summary
