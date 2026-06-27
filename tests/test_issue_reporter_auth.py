"""TDD: issue-reporter auth — submission must not 401 after a uvicorn reload.

Regression: when uvicorn runs with --reload (the dev/staging mode), every
file write re-imports project_forge.web.app, regenerating the in-memory
_dashboard_token. Any tab still open with the previous page-rendered
meta-tag token then 401s on its next POST (e.g. submitting a feature
request via the issue reporter).

Fix contract:
- Dashboard token persists across module reloads in the same process
  (only a fresh process produces a fresh token).
- Issue-reporter POST with the page-rendered token always succeeds for
  the lifetime of that process.

CI gate: this file. Don't let this regress.
"""

from __future__ import annotations

import importlib
import os

import pytest

# ── Token persists across uvicorn --reload ──────────────────────────


class TestDashboardTokenPersistsAcrossReload:
    """Module reload (uvicorn --reload pattern) must NOT regenerate the token."""

    def test_token_survives_module_reload(self, monkeypatch):
        # Clear any stale state
        monkeypatch.delenv("FORGE_DASHBOARD_TOKEN_RUNTIME", raising=False)

        from project_forge.web import app as app_mod

        # Save originals so we can restore module state afterward
        original_db = app_mod.db
        original_token = app_mod._dashboard_token
        try:
            importlib.reload(app_mod)
            first_token = app_mod._dashboard_token

            # Simulate a uvicorn --reload re-import of the module
            importlib.reload(app_mod)
            second_token = app_mod._dashboard_token

            assert first_token == second_token, (
                f"Dashboard token regenerated on module reload "
                f"({first_token[:8]}... → {second_token[:8]}...) — "
                f"open browser tabs would 401 on their next POST."
            )
        finally:
            # Restore: prevent module-level state pollution affecting other tests
            app_mod.db = original_db
            app_mod._dashboard_token = original_token
            os.environ["FORGE_DASHBOARD_TOKEN_RUNTIME"] = original_token

    def test_token_is_set_in_env_for_persistence(self, monkeypatch):
        monkeypatch.delenv("FORGE_DASHBOARD_TOKEN_RUNTIME", raising=False)

        from project_forge.web import app as app_mod

        original_db = app_mod.db
        original_token = app_mod._dashboard_token
        try:
            importlib.reload(app_mod)

            # The fix mechanism must use a runtime env var so subsequent
            # reloads find it
            env_token = os.environ.get("FORGE_DASHBOARD_TOKEN_RUNTIME")
            assert env_token == app_mod._dashboard_token
        finally:
            app_mod.db = original_db
            app_mod._dashboard_token = original_token
            os.environ["FORGE_DASHBOARD_TOKEN_RUNTIME"] = original_token


# ── Issue-reporter POST with the rendered token must not 401 ────────


@pytest.mark.asyncio
async def test_issue_report_post_with_dashboard_token_does_not_401(monkeypatch):
    """Posting an issue with the meta-tag dashboard token must succeed
    (or fail-because-of-github not 401-because-of-auth).
    """
    from project_forge.config import settings
    from project_forge.web import app as app_mod

    # Enable auth by setting a real api_token. Pydantic Settings is an
    # instance, so we mutate via setattr; monkeypatch reverts on teardown.
    monkeypatch.setattr(settings, "api_token", "test-api-token-for-CI")

    # Stub GitHub create_issue so we don't hit the network
    async def _none_async(*args, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr("project_forge.web.routes.create_gh_issue", _none_async)

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_mod.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        token = app_mod._dashboard_token  # what the meta tag renders

        resp = await client.post(
            "/api/issues/report",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "issue_type": "feature",
                "description": "Add vertical filter to explore page.",
                "page_url": "/",
                "severity": "low",
            },
        )

        # Anything but 401 is acceptable for the auth contract.
        assert resp.status_code != 401, (
            f"Issue report rejected as Unauthorized (token={token[:8]}...). Body: {resp.text}"
        )


@pytest.mark.asyncio
async def test_issue_report_post_with_wrong_token_returns_401(monkeypatch):
    """Sanity: actually-wrong token DOES 401 (proves middleware functioning)."""
    from project_forge.config import settings
    from project_forge.web import app as app_mod

    monkeypatch.setattr(settings, "api_token", "test-api-token-for-CI")

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app_mod.app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/api/issues/report",
            headers={"Authorization": "Bearer wrong-token-on-purpose"},
            json={
                "issue_type": "feature",
                "description": "x",
                "page_url": "/",
                "severity": "low",
            },
        )

        assert resp.status_code == 401, (
            f"Expected 401 for wrong token; middleware appears broken (got {resp.status_code})."
        )
