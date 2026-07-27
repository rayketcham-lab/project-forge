"""Mechanic run status channel (#100) — file-based progress for the panel.

A mechanic run is a detached process that takes minutes; it writes its stage
to a JSON file the /mechanic page polls (and animates). These pin the
write/read round-trip, staleness, the endpoint, and that a real cycle emits
a terminal stage.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "status.db")
    await database.connect()
    yield database
    await database.close()


@pytest.fixture(autouse=True)
def _no_open_prs(monkeypatch):
    # run_mechanic_cycle looks up open PRs to skip already-worked items; keep
    # the cycle tests off gh.
    monkeypatch.setattr("project_forge.engine.mechanic_review.list_open_prs", lambda: [])


class TestStatusRoundTrip:
    def test_write_then_read_renders_message(self, tmp_path, monkeypatch):
        import project_forge.engine.mechanic_status as ms

        monkeypatch.setattr(ms, "_STATUS_FILE", tmp_path / "status.json")
        ms.write_status("implementing", item="Fix the thing")
        data = ms.read_status()
        assert data["stage"] == "implementing"
        assert data["terminal"] is False
        assert "Fix the thing" in data["message"]

    def test_no_file_is_idle(self, tmp_path, monkeypatch):
        import project_forge.engine.mechanic_status as ms

        monkeypatch.setattr(ms, "_STATUS_FILE", tmp_path / "nope.json")
        data = ms.read_status()
        assert data["stage"] == "idle"
        assert data["terminal"] is True

    def test_stale_nonterminal_becomes_idle(self, tmp_path, monkeypatch):
        import project_forge.engine.mechanic_status as ms

        f = tmp_path / "status.json"
        monkeypatch.setattr(ms, "_STATUS_FILE", f)
        f.write_text(
            json.dumps({"stage": "implementing", "item": "x", "terminal": False, "updated_at": time.time() - 99999})
        )
        assert ms.read_status()["stage"] == "idle"

    def test_terminal_pr_opened_carries_detail(self, tmp_path, monkeypatch):
        import project_forge.engine.mechanic_status as ms

        monkeypatch.setattr(ms, "_STATUS_FILE", tmp_path / "status.json")
        ms.write_status("pr_opened", item="X", detail="https://gh/pr/9")
        data = ms.read_status()
        assert data["stage"] == "pr_opened"
        assert data["terminal"] is True
        assert data["detail"] == "https://gh/pr/9"


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "status_routes.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_endpoint_returns_current(self, client):
        with patch(
            "project_forge.engine.mechanic_status.read_status",
            return_value={"stage": "gating", "message": "Running tests…", "terminal": False},
        ):
            resp = await client.get("/api/mechanic/status")
        assert resp.status_code == 200
        assert resp.json()["stage"] == "gating"


class TestCycleEmitsStages:
    @pytest.mark.asyncio
    async def test_no_work_writes_terminal_status(self, db, tmp_path, monkeypatch):
        import project_forge.engine.mechanic_status as ms
        from project_forge.engine.mechanic import run_mechanic_cycle

        monkeypatch.setattr(ms, "_STATUS_FILE", tmp_path / "status.json")
        result = await run_mechanic_cycle(db)  # empty queue -> no_work
        assert result.status == "no_work"
        assert ms.read_status()["stage"] == "no_work"

    @pytest.mark.asyncio
    async def test_gate_failure_writes_gate_failed_status(self, db, tmp_path, monkeypatch):
        import project_forge.engine.mechanic as m
        import project_forge.engine.mechanic_status as ms

        monkeypatch.setattr(ms, "_STATUS_FILE", tmp_path / "status.json")
        si = Idea(
            name="Some SI",
            tagline="fix a thing",
            description="A concrete self-improvement.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="reliability",
            feasibility_score=0.6,
            mvp_scope="one change",
            tech_stack=["python"],
            content_hash="stat-si",
        )
        await db.save_idea(si)

        class _Proc:
            returncode = 0
            stdout = ""
            stderr = ""

        from pathlib import Path

        monkeypatch.setattr(m, "_create_workspace", lambda branch: Path("mech-ws"))
        monkeypatch.setattr(m, "run_agent", lambda wt, prompt, **kw: _Proc())
        monkeypatch.setattr(m, "_changed_paths", lambda wt: ["src/project_forge/engine/x.py"])
        monkeypatch.setattr(m, "_quality_gate", lambda wt: (False, "pytest failed"))
        monkeypatch.setattr(m, "_remove_workspace", lambda wt: None)

        result = await m.run_mechanic_cycle(db)
        assert result.status == "gate_failed"
        assert ms.read_status()["stage"] == "gate_failed"
