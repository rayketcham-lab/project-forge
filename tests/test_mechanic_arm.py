"""Forge Mechanic arming (#100) — one-shot runner, cadence, manual trigger.

The mechanic writes code and opens PRs autonomously, so its cadence is
DISARMED by default and every run is a detached one-shot process (the server
never blocks on the agent). These pin the arm-switch, the spawn plumbing,
and the human "Run now" endpoint.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


class TestOneShotRunner:
    @pytest.mark.asyncio
    async def test_run_once_wires_cycle_and_closes_db(self, monkeypatch):
        import project_forge.cron.mechanic_runner as mr
        from project_forge.engine.mechanic import MechanicResult

        fake_db = MagicMock()
        fake_db.connect = AsyncMock()
        fake_db.close = AsyncMock()
        monkeypatch.setattr(mr, "Database", lambda path: fake_db)

        async def _fake_cycle(db):
            return MechanicResult("id1", "Fix thing", "pr_opened", pr_url="https://gh/pr/1")

        monkeypatch.setattr(mr, "run_mechanic_cycle", _fake_cycle)

        result = await mr.run_once()
        assert result["status"] == "pr_opened"
        assert result["pr_url"].endswith("/pr/1")
        fake_db.close.assert_awaited_once()

    def test_spawn_launches_detached_module(self, monkeypatch):
        import project_forge.cron.mechanic_runner as mr

        captured = {}

        def _fake_popen(argv, **kw):
            captured["argv"] = argv
            captured["kw"] = kw
            return MagicMock()

        monkeypatch.setattr(mr.subprocess, "Popen", _fake_popen)
        mr.spawn_mechanic_run()

        assert "project_forge.cron.mechanic_runner" in captured["argv"]
        assert captured["kw"].get("start_new_session") is True


class TestCadenceArming:
    def test_cadence_registered_and_disarmed_by_default(self, monkeypatch):
        from project_forge.web.lifespan_scheduler import _mechanic_armed, default_cadences

        monkeypatch.delenv("FORGE_MECHANIC_ENABLED", raising=False)
        names = {c.name for c in default_cadences()}
        assert "mechanic" in names
        assert _mechanic_armed() is False

    @pytest.mark.asyncio
    async def test_fire_mechanic_disarmed_does_not_spawn(self, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.delenv("FORGE_MECHANIC_ENABLED", raising=False)
        called = {"n": 0}
        monkeypatch.setattr(
            "project_forge.cron.mechanic_runner.spawn_mechanic_run",
            lambda: called.__setitem__("n", called["n"] + 1),
        )
        await ls._fire_mechanic(None)
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_fire_mechanic_armed_spawns_once(self, monkeypatch):
        from project_forge.web import lifespan_scheduler as ls

        monkeypatch.setenv("FORGE_MECHANIC_ENABLED", "1")
        called = {"n": 0}
        monkeypatch.setattr(
            "project_forge.cron.mechanic_runner.spawn_mechanic_run",
            lambda: called.__setitem__("n", called["n"] + 1),
        )
        await ls._fire_mechanic(None)
        assert called["n"] == 1


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "mechanic_arm.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as cl:
        yield cl
    await db.close()


class TestRunEndpoint:
    @pytest.mark.asyncio
    async def test_run_endpoint_spawns_when_idle(self, client):
        with (
            patch("project_forge.engine.mechanic_status.read_status", return_value={"terminal": True}),
            patch("project_forge.engine.mechanic_status.write_status"),
            patch("project_forge.cron.mechanic_runner.spawn_mechanic_run") as sp,
        ):
            resp = await client.post("/api/mechanic/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "started"
        sp.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_endpoint_refuses_when_already_running(self, client):
        """Guard: no second concurrent run (double spend + branch race)."""
        with (
            patch(
                "project_forge.engine.mechanic_status.read_status",
                return_value={"terminal": False, "message": "busy"},
            ),
            patch("project_forge.cron.mechanic_runner.spawn_mechanic_run") as sp,
        ):
            resp = await client.post("/api/mechanic/run")
        assert resp.json()["status"] == "already_running"
        sp.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_endpoint_force_overrides_guard(self, client):
        """force=true starts a run even if one looks in-progress (the escape
        hatch for a stuck run)."""
        with (
            patch(
                "project_forge.engine.mechanic_status.read_status",
                return_value={"terminal": False, "message": "busy"},
            ),
            patch("project_forge.engine.mechanic_status.write_status"),
            patch("project_forge.cron.mechanic_runner.spawn_mechanic_run") as sp,
        ):
            resp = await client.post("/api/mechanic/run?force=true")
        assert resp.json()["status"] == "started"
        sp.assert_called_once()
