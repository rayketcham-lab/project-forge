"""Tests for the delete-project feature.

Covers:
1. db.delete_idea removes the row from SQLite
2. GET /api/ideas/{id}/check-repo returns exists:false when idea has no repo URL
3. GET /api/ideas/{id}/check-repo calls gh and returns the correct result (mock subprocess)
4. DELETE /api/ideas/{id} is blocked (409) when gh reports the repo exists
5. DELETE /api/ideas/{id} succeeds when gh reports the repo is gone
"""

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scaffolded_idea(**kw) -> Idea:
    defaults = dict(
        name="Test Scaffolded Project",
        tagline="A scaffolded test project",
        description="Description of the project.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Developers need this.",
        feasibility_score=0.8,
        mvp_scope="Build the core feature.",
        tech_stack=["python", "fastapi"],
        status="scaffolded",
    )
    defaults.update(kw)
    return Idea(**defaults)


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_delete.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ---------------------------------------------------------------------------
# 1. db.delete_idea removes the row
# ---------------------------------------------------------------------------


class TestDeleteIdeaDb:
    @pytest.mark.asyncio
    async def test_delete_idea_removes_row(self, db):
        """delete_idea removes the idea so get_idea returns None."""
        idea = _scaffolded_idea()
        await db.save_idea(idea)

        assert await db.get_idea(idea.id) is not None

        await db.delete_idea(idea.id)

        assert await db.get_idea(idea.id) is None

    @pytest.mark.asyncio
    async def test_delete_idea_noop_on_missing(self, db):
        """delete_idea on a non-existent id raises no exception."""
        await db.delete_idea("nonexistent-id-xyz")  # must not raise


# ---------------------------------------------------------------------------
# 2. check-repo: idea has no repo URL → exists: false
# ---------------------------------------------------------------------------


class TestCheckRepoNoUrl:
    @pytest.mark.asyncio
    async def test_check_repo_no_url_returns_false(self, client):
        """When idea has no project_repo_url, check-repo returns exists: false."""
        idea = _scaffolded_idea(project_repo_url=None)
        await db.save_idea(idea)

        r = await client.get(f"/api/ideas/{idea.id}/check-repo")
        assert r.status_code == 200
        data = r.json()
        assert data["exists"] is False
        assert data["repo"] is None

    @pytest.mark.asyncio
    async def test_check_repo_returns_404_for_unknown_idea(self, client):
        """check-repo on an unknown idea returns 404."""
        r = await client.get("/api/ideas/no-such-idea/check-repo")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 3. check-repo: calls gh and returns the correct result
# ---------------------------------------------------------------------------


class TestCheckRepoCallsGh:
    @pytest.mark.asyncio
    async def test_check_repo_exists_when_gh_returns_zero(self, client):
        """When gh exits 0, check-repo returns exists: true."""
        idea = _scaffolded_idea(project_repo_url="https://github.com/acme/my-repo")
        await db.save_idea(idea)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("project_forge.web.routes.subprocess.run", return_value=mock_result) as mock_run:
            r = await client.get(f"/api/ideas/{idea.id}/check-repo")

        assert r.status_code == 200
        data = r.json()
        assert data["exists"] is True
        assert data["repo"] == "acme/my-repo"
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "acme/my-repo" in call_args

    @pytest.mark.asyncio
    async def test_check_repo_gone_when_gh_returns_nonzero(self, client):
        """When gh exits non-zero, check-repo returns exists: false."""
        idea = _scaffolded_idea(project_repo_url="https://github.com/acme/deleted-repo")
        await db.save_idea(idea)

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("project_forge.web.routes.subprocess.run", return_value=mock_result):
            r = await client.get(f"/api/ideas/{idea.id}/check-repo")

        assert r.status_code == 200
        data = r.json()
        assert data["exists"] is False
        assert data["repo"] == "acme/deleted-repo"


# ---------------------------------------------------------------------------
# 4. DELETE endpoint blocked when repo exists (gh exit 0)
# ---------------------------------------------------------------------------


class TestDeleteEndpointBlocked:
    @pytest.mark.asyncio
    async def test_delete_blocked_when_repo_exists(self, client):
        """DELETE returns 409 when gh reports the repo still exists."""
        idea = _scaffolded_idea(project_repo_url="https://github.com/acme/live-repo")
        await db.save_idea(idea)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("project_forge.web.routes.subprocess.run", return_value=mock_result):
            r = await client.delete(f"/api/ideas/{idea.id}")

        assert r.status_code == 409
        assert "live-repo" in r.json()["detail"]

        # Idea must still exist in DB
        assert await db.get_idea(idea.id) is not None


# ---------------------------------------------------------------------------
# 5. DELETE endpoint succeeds when repo is gone (gh exit 1)
# ---------------------------------------------------------------------------


class TestDeleteEndpointSucceeds:
    @pytest.mark.asyncio
    async def test_delete_succeeds_when_repo_gone(self, client):
        """DELETE returns 200 and removes the idea when repo is not found."""
        idea = _scaffolded_idea(project_repo_url="https://github.com/acme/gone-repo")
        await db.save_idea(idea)

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("project_forge.web.routes.subprocess.run", return_value=mock_result):
            r = await client.delete(f"/api/ideas/{idea.id}")

        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "deleted"
        assert data["id"] == idea.id

        # Idea must be gone from DB
        assert await db.get_idea(idea.id) is None

    @pytest.mark.asyncio
    async def test_delete_succeeds_when_no_repo_url(self, client):
        """DELETE returns 200 for an idea with no repo URL (no gh check needed)."""
        idea = _scaffolded_idea(project_repo_url=None)
        await db.save_idea(idea)

        r = await client.delete(f"/api/ideas/{idea.id}")

        assert r.status_code == 200
        assert r.json()["status"] == "deleted"
        assert await db.get_idea(idea.id) is None

    @pytest.mark.asyncio
    async def test_delete_returns_404_for_unknown_idea(self, client):
        """DELETE on an unknown idea returns 404."""
        r = await client.delete("/api/ideas/no-such-id")
        assert r.status_code == 404
