"""Tests for dashboard auth — browser requests must work when api_token is set (#46).

The BearerTokenMiddleware blocks POST requests without a valid token, but
the dashboard's own JS needs to call POST endpoints (challenge, approve,
reject, scaffold, promote). The fix: inject the token into the page so
getAuthHeaders() can include it.

Also covers #47 — richer static introspection proposals with module details.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_dash_auth.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


@pytest_asyncio.fixture
async def idea_in_db(client):
    """Store an idea and return it."""
    idea = Idea(
        name="Auth Test Idea",
        tagline="test",
        description="test description",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="test market",
        feasibility_score=0.7,
        mvp_scope="test scope",
        tech_stack=["python"],
    )
    await db.save_idea(idea)
    return idea


# ---------------------------------------------------------------------------
# #46 — Dashboard auth: token injected into pages
# ---------------------------------------------------------------------------


class TestDashboardTokenInjection:
    """Dashboard pages should include the API token so JS can authenticate."""

    @pytest.mark.asyncio
    async def test_base_template_includes_api_token_meta(self, client):
        """HTML pages should have a meta tag with the API token."""
        resp = await client.get("/")
        assert resp.status_code == 200
        # The token should be in a meta tag for JS to read
        assert 'name="forge-token"' in resp.text

    @pytest.mark.asyncio
    async def test_idea_detail_includes_token(self, client, idea_in_db):
        """Idea detail page should include the token meta tag."""
        resp = await client.get(f"/ideas/{idea_in_db.id}")
        assert resp.status_code == 200
        assert 'name="forge-token"' in resp.text


class TestDashboardAuthHeaders:
    """getAuthHeaders() in app.js should send the Bearer token from the meta tag."""

    @pytest.mark.asyncio
    async def test_app_js_reads_token_from_meta(self, client):
        """app.js getAuthHeaders() should read token from meta tag."""
        resp = await client.get("/static/app.js")
        assert resp.status_code == 200
        # Should reference the meta tag to get the token
        assert "forge-token" in resp.text
        assert "Bearer" in resp.text


class TestChallengeWithToken:
    """Challenge POST should succeed when Bearer token is included."""

    @pytest.mark.asyncio
    async def test_challenge_post_with_dashboard_token_succeeds(self, client, idea_in_db):
        """POST /api/ideas/{id}/challenge should work with dashboard token."""
        from project_forge.web.app import _dashboard_token

        headers = {"Authorization": f"Bearer {_dashboard_token}"}

        with patch("project_forge.web.routes._challenge_idea") as mock_challenge:
            mock_challenge.return_value = {
                "response": "Good point",
                "verdict": "no_change",
                "confidence": 0.7,
                "changes": [],
            }
            resp = await client.post(
                f"/api/ideas/{idea_in_db.id}/challenge",
                json={
                    "question": "Is this feasible?",
                    "challenge_type": "feasibility",
                    "focus_area": "all",
                    "tone": "skeptical",
                },
                headers=headers,
            )

        assert resp.status_code == 200, f"Challenge failed: {resp.text}"
        data = resp.json()
        assert data["response"] == "Good point"

    @pytest.mark.asyncio
    async def test_challenge_post_without_token_returns_401(self, client, idea_in_db):
        """POST without token should return 401 when api_token is configured."""
        from project_forge.config import settings

        if not settings.api_token:
            pytest.skip("api_token not configured")

        resp = await client.post(
            f"/api/ideas/{idea_in_db.id}/challenge",
            json={
                "question": "test",
                "challenge_type": "freeform",
                "focus_area": "all",
                "tone": "curious",
            },
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# #47 — Richer static introspection proposals
# ---------------------------------------------------------------------------


class TestRicherStaticProposals:
    """Static proposals should include module-specific details."""

    def test_proposals_include_function_names(self):
        """Proposals for untested modules should list function/class names."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        # At least some proposals should mention specific functions or classes
        has_specifics = False
        for idea in proposals:
            text = f"{idea.description} {idea.mvp_scope}"
            # Should mention def/class names, not just generic text
            if "def " in text or "class " in text or "()" in text:
                has_specifics = True
                break
        assert has_specifics, "No proposals mention specific functions or classes"

    def test_proposals_include_docstring_info(self):
        """Untested-module proposals should include the module's docstring."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        test_proposals = [p for p in proposals if p.name.startswith("Add tests for")]
        has_docstring = False
        for idea in test_proposals:
            # Description should include what the module does, not just "no test file"
            if "no corresponding test file" not in idea.description:
                has_docstring = True
                break
        assert has_docstring, "All test proposals just say 'no corresponding test file'"

    def test_find_untested_modules_includes_details(self):
        """find_untested_modules should return function/class counts."""
        from project_forge.engine.static_introspect import find_untested_modules

        findings = find_untested_modules(PROJECT_ROOT)
        assert len(findings) > 0
        for f in findings:
            assert "functions" in f, f"Finding for {f['module']} missing function count"
            assert "classes" in f, f"Finding for {f['module']} missing class count"
            assert isinstance(f["functions"], list)
            assert isinstance(f["classes"], list)

    def test_proposals_feasibility_varies_by_complexity(self):
        """Feasibility score should vary based on module complexity."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        test_proposals = [p for p in proposals if p.name.startswith("Add tests for")]
        if len(test_proposals) < 2:
            pytest.skip("Need at least 2 test proposals to compare scores")
        scores = {p.feasibility_score for p in test_proposals}
        assert len(scores) > 1, "All test proposals have the same feasibility score"

    def test_mvp_scope_lists_specific_tests(self):
        """mvp_scope should list specific test cases, not just 'cover the public API'."""
        from project_forge.engine.static_introspect import generate_static_proposals

        proposals = generate_static_proposals(PROJECT_ROOT)
        test_proposals = [p for p in proposals if p.name.startswith("Add tests for")]
        has_specific = False
        for p in test_proposals:
            if "test_" in p.mvp_scope and ("def " in p.mvp_scope or "()" in p.mvp_scope):
                has_specific = True
                break
        assert has_specific, "No proposal mvp_scope lists specific test cases"
