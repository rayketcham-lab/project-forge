"""Forge Mechanic review panel (#100) — gh wrappers + routes.

The operator's gate: list the mechanic's open PRs, approve (squash-merge) or
reject (close). Nothing merges without the button. gh + subprocess mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _proc(returncode=0, stdout="", stderr=""):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


# --------------------------------------------------------------------------- #
# gh wrappers                                                                 #
# --------------------------------------------------------------------------- #


class TestCiState:
    def test_states(self):
        from project_forge.engine.mechanic_review import _ci_state

        assert _ci_state(None) == "none"
        assert _ci_state([{"conclusion": "SUCCESS"}]) == "passing"
        assert _ci_state([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == "failing"
        assert _ci_state([{"status": "IN_PROGRESS"}]) == "pending"


class TestListOpenPrs:
    def test_filters_to_mechanic_branches(self):
        from project_forge.engine import mechanic_review as mr

        rows = [
            {
                "number": 7,
                "title": "[Mechanic] Redact token leak",
                "url": "https://gh/pr/7",
                "headRefName": "mechanic/abc123",
                "additions": 20,
                "deletions": 2,
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
            },
            {
                "number": 8,
                "title": "some human PR",
                "url": "https://gh/pr/8",
                "headRefName": "feature/whatever",
                "statusCheckRollup": [],
            },
        ]
        with patch.object(mr, "_gh", return_value=_proc(0, json.dumps(rows))):
            prs = mr.list_open_prs()
        assert len(prs) == 1
        assert prs[0]["number"] == 7
        assert prs[0]["item_id"] == "abc123"
        assert prs[0]["ci"] == "passing"

    def test_gh_failure_returns_empty(self):
        from project_forge.engine import mechanic_review as mr

        with patch.object(mr, "_gh", return_value=_proc(1, "", "gh: not authed")):
            assert mr.list_open_prs() == []

    def test_bad_json_returns_empty(self):
        from project_forge.engine import mechanic_review as mr

        with patch.object(mr, "_gh", return_value=_proc(0, "not json")):
            assert mr.list_open_prs() == []


class TestMergeClose:
    def test_merge_ok(self):
        from project_forge.engine import mechanic_review as mr

        with patch.object(mr, "_gh", return_value=_proc(0, "merged")) as gh:
            result = mr.merge_pr(7)
        assert result["ok"] is True
        # squash + delete-branch, never a plain merge
        args = gh.call_args[0][0]
        assert "--squash" in args and "--delete-branch" in args

    def test_merge_failure_surfaces(self):
        from project_forge.engine import mechanic_review as mr

        with patch.object(mr, "_gh", return_value=_proc(1, "", "not mergeable")):
            result = mr.merge_pr(7)
        assert result["ok"] is False
        assert "not mergeable" in result["detail"]

    def test_close_ok(self):
        from project_forge.engine import mechanic_review as mr

        with patch.object(mr, "_gh", return_value=_proc(0, "closed")):
            assert mr.close_pr(7)["ok"] is True


# --------------------------------------------------------------------------- #
# routes                                                                      #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "mechanic_routes.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestMechanicRoutes:
    @pytest.mark.asyncio
    async def test_page_renders_prs(self, client):
        prs = [
            {
                "number": 7,
                "title": "[Mechanic] Fix X",
                "url": "u",
                "item_id": "abc",
                "additions": 3,
                "deletions": 1,
                "ci": "passing",
            }
        ]
        with patch("project_forge.engine.mechanic_review.list_open_prs", return_value=prs):
            resp = await client.get("/mechanic")
        assert resp.status_code == 200
        assert "[Mechanic] Fix X" in resp.text
        assert "abc" in resp.text

    @pytest.mark.asyncio
    async def test_api_list(self, client):
        with patch("project_forge.engine.mechanic_review.list_open_prs", return_value=[]):
            resp = await client.get("/api/mechanic/prs")
        assert resp.status_code == 200
        assert resp.json() == {"prs": []}

    @pytest.mark.asyncio
    async def test_approve_merges(self, client):
        with patch(
            "project_forge.engine.mechanic_review.merge_pr", return_value={"ok": True, "detail": "merged"}
        ) as mk:
            resp = await client.post("/api/mechanic/prs/7/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "merged"
        mk.assert_called_once_with(7)

    @pytest.mark.asyncio
    async def test_approve_surfaces_merge_failure(self, client):
        with patch("project_forge.engine.mechanic_review.merge_pr", return_value={"ok": False, "detail": "conflict"}):
            resp = await client.post("/api/mechanic/prs/7/approve")
        assert resp.status_code == 502

    @pytest.mark.asyncio
    async def test_reject_closes(self, client):
        with patch(
            "project_forge.engine.mechanic_review.close_pr", return_value={"ok": True, "detail": "closed"}
        ) as ck:
            resp = await client.post("/api/mechanic/prs/9/reject")
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"
        ck.assert_called_once_with(9)
