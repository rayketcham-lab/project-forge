"""Route tests for the Foundry one-click 'Create repo' flow (v0.17).

The actual GitHub calls are mocked — we verify the wiring: gating by status,
404 on missing, the proven scaffold path is invoked, the Foundry plan's
issues are filed, and the idea is stamped scaffolded with its repo URL.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory


def _idea(status="new", **over) -> Idea:
    idea = Idea(
        name="Goalpost",
        tagline="flat-fee conversion backend",
        description="d" * 80,
        category=IdeaCategory.MICRO_SAAS,
        market_analysis="m" * 40,
        feasibility_score=0.8,
        mvp_scope="Phase 1: ingest. Phase 2: dashboard.",
        tech_stack=["typescript"],
        **over,
    )
    idea.status = status
    return idea


class _Spec:
    repo_name = "goalpost"
    initial_issues = []


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app
    from project_forge.web.app import db as appdb

    appdb.db_path = tmp_path / "foundry_create.db"
    await appdb.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, appdb
    await appdb.close()


@pytest.mark.asyncio
async def test_create_builds_repo_and_stamps_idea(client, tmp_path):
    c, appdb = client
    idea = _idea(status="new", content_hash="fc1")
    await appdb.save_idea(idea)

    plan = {"starter_issues": [{"title": "Phase 1", "body": "build the beachhead"}]}
    with (
        patch("project_forge.engine.foundry.build_scaffold_plan", return_value=plan),
        patch("project_forge.scaffold.builder.build_scaffold_spec", return_value=_Spec()),
        patch("project_forge.scaffold.builder.render_scaffold", return_value=Path(tmp_path)),
        patch("project_forge.scaffold.github.create_repo", return_value="https://github.com/x/goalpost") as cr,
        patch("project_forge.scaffold.github.push_initial_commit") as push,
        patch("project_forge.scaffold.github.create_issue") as ci,
    ):
        resp = await c.post(f"/api/foundry/create/{idea.id}")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "scaffolded"
    assert data["repo_url"] == "https://github.com/x/goalpost"
    cr.assert_called_once()
    push.assert_called_once()
    assert ci.call_count >= 1  # the Foundry plan's heuristic issues were filed
    got = await appdb.get_idea(idea.id)
    assert got.status == "scaffolded"
    assert got.project_repo_url == "https://github.com/x/goalpost"


@pytest.mark.asyncio
async def test_create_404_for_missing(client):
    c, _ = client
    resp = await c.post("/api/foundry/create/deadbeef")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_rejects_wrong_status(client):
    c, appdb = client
    idea = _idea(status="scaffolded", content_hash="fc2")
    await appdb.save_idea(idea)
    resp = await c.post(f"/api/foundry/create/{idea.id}")
    assert resp.status_code == 400
