"""Tests for all Priority 1 blockers from team review.

Covers:
1. Path traversal protection in apply_changes
2. Entry point (main) for self_improve_runner
3. Idempotency guard on approve/promote (no double GH issues)
4. CI job ordering (self-improvement-queue needs: [test])
5. Scoped revert (only runner-touched files, not git checkout .)
"""

import inspect  # noqa: I001
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_p1.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


def _si_idea(**overrides) -> Idea:
    defaults = dict(
        name="Test SI Idea",
        tagline="unique tagline for p1 test",
        description="Test.",
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Internal.",
        feasibility_score=0.8,
        mvp_scope="Build it.",
        tech_stack=["python"],
        status="new",
    )
    defaults.update(overrides)
    return Idea(**defaults)


# ===================================================================
# 1. PATH TRAVERSAL PROTECTION
# ===================================================================


class TestPathTraversalProtection:
    """apply_changes must reject paths that escape the project root."""

    def test_rejects_absolute_path(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "/etc/passwd", "action": "create", "content": "hacked"}]
        with pytest.raises(ValueError, match="outside project root"):
            apply_changes(changes, project_root=tmp_path)

    def test_rejects_dotdot_traversal(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "../../etc/cron.d/evil", "action": "create", "content": "bad"}]
        with pytest.raises(ValueError, match="outside project root"):
            apply_changes(changes, project_root=tmp_path)

    def test_rejects_dotdot_in_edit(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        (tmp_path / "legit.py").write_text("x = 1\n")
        changes = [
            {
                "path": "../../../outside.py",
                "action": "edit",
                "search": "x = 1",
                "replace": "x = 2",
            }
        ]
        with pytest.raises(ValueError, match="outside project root"):
            apply_changes(changes, project_root=tmp_path)

    def test_allows_valid_nested_path(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "src/project_forge/engine/file.py", "action": "create", "content": "ok\n"}]
        apply_changes(changes, project_root=tmp_path)
        assert (tmp_path / "src" / "project_forge" / "engine" / "file.py").exists()

    def test_rejects_unknown_action(self, tmp_path):
        from project_forge.cron.self_improve_runner import apply_changes

        changes = [{"path": "tests/file.py", "action": "delete", "content": ""}]
        with pytest.raises(ValueError, match="Unknown action"):
            apply_changes(changes, project_root=tmp_path)


# ===================================================================
# 2. ENTRY POINT FOR SELF_IMPROVE_RUNNER
# ===================================================================


class TestSelfImproveRunnerEntryPoint:
    """self_improve_runner must have a main() callable for console_scripts."""

    def test_has_main_function(self):
        from project_forge.cron import self_improve_runner

        assert hasattr(self_improve_runner, "main")
        assert callable(self_improve_runner.main)

    def test_pyproject_has_forge_self_improve_script(self):
        pyproject = Path("/opt/vmdata/project-forge/pyproject.toml")
        content = pyproject.read_text()
        assert "forge-self-improve" in content

    def test_ci_build_job_checks_forge_self_improve(self):
        ci = Path("/opt/vmdata/project-forge/.github/workflows/ci.yml")
        content = ci.read_text()
        assert "forge-self-improve" in content


# ===================================================================
# 3. IDEMPOTENCY: NO DOUBLE GH ISSUES
# ===================================================================


class TestApproveIdempotency:
    """Approving an already-approved SI idea must not create a second GH issue."""

    @pytest.mark.asyncio
    async def test_approve_already_approved_si_does_not_create_issue(self, client):
        """If idea is already approved with a github_issue_url, don't create another."""
        idea = _si_idea(
            status="approved",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/99",
        )
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue") as mock_create:
            resp = await client.post(f"/ideas/{idea.id}/approve")

        # Should NOT call create_issue again
        mock_create.assert_not_called()
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_approve_new_si_creates_issue(self, client):
        """Approving a new SI idea should still create the issue."""
        idea = _si_idea(name="Fresh idea", tagline="fresh unique tagline here")
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue") as mock_create:
            mock_create.return_value = "https://github.com/rayketcham-lab/project-forge/issues/100"
            resp = await client.post(f"/ideas/{idea.id}/approve")

        assert resp.status_code == 200
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_si_github_failure_returns_error_not_500(self, client):
        """If GH issue creation fails, return a structured error, not bare 500."""
        idea = _si_idea(name="Fail idea", tagline="unique fail test tagline")
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue", side_effect=RuntimeError("gh auth expired")):
            resp = await client.post(f"/ideas/{idea.id}/approve")

        # Should return an error status, not crash with unhandled 500
        assert resp.status_code in (200, 502)
        data = resp.json()
        # Status should NOT be "approved" since the GH issue wasn't created
        if resp.status_code == 502:
            assert "gh auth expired" in data.get("detail", "")
        # The idea should still be "new" in DB
        updated = await db.get_idea(idea.id)
        assert updated.status == "new"

    @pytest.mark.asyncio
    async def test_promote_already_promoted_does_not_create_issue(self, client):
        """Promoting an already-promoted idea should be idempotent."""
        idea = _si_idea(
            name="Already promoted",
            tagline="unique promoted tagline",
            status="approved",
            github_issue_url="https://github.com/rayketcham-lab/project-forge/issues/99",
        )
        await db.save_idea(idea)

        with patch("project_forge.web.routes.create_issue") as mock_create:
            resp = await client.post(f"/api/thinktank/{idea.id}/promote")

        mock_create.assert_not_called()
        assert resp.status_code == 200


# ===================================================================
# 4. CI JOB ORDERING
# ===================================================================


class TestCIJobOrdering:
    """self-improvement-queue job must depend on test job."""

    def test_self_improvement_queue_needs_test(self):
        ci_path = Path("/opt/vmdata/project-forge/.github/workflows/ci.yml")
        ci = yaml.safe_load(ci_path.read_text())
        job = ci["jobs"]["self-improvement-queue"]
        needs = job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "test" in needs, f"self-improvement-queue needs: {needs}, expected 'test'"


# ===================================================================
# 5. SCOPED REVERT (only runner-touched files)
# ===================================================================


class TestScopedRevert:
    """_revert_changes should only revert specified files, not git checkout ."""

    def test_revert_accepts_file_list(self):
        from project_forge.cron.self_improve_runner import _revert_changes

        sig = inspect.signature(_revert_changes)
        params = list(sig.parameters.keys())
        assert "changed_files" in params, f"_revert_changes params: {params}"

    def test_revert_with_empty_list_does_nothing(self):
        from project_forge.cron.self_improve_runner import _revert_changes

        with patch("project_forge.cron.self_improve_runner.subprocess") as mock_sub:
            _revert_changes(changed_files=[])

        # Should NOT run git checkout when no files to revert
        mock_sub.run.assert_not_called()
