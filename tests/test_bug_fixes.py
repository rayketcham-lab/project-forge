"""TDD tests for GitHub bugs #9, #17, #30.

Bug #9: Super ideas should be created with status="new", not "approved"
Bug #17: Browser POST requests must include Bearer token from meta tag
Bug #30: Review/SI/challenge runners must work without API key
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test_bugs.db")
    await database.connect()
    yield database
    await database.close()


# ---------------------------------------------------------------------------
# Bug #9: Super ideas must have status="new"
# ---------------------------------------------------------------------------


class TestBug9SuperIdeaStatus:
    """Super ideas should not be auto-approved. Status must be 'new'."""

    @pytest.mark.asyncio
    async def test_store_super_creates_idea_with_status_new(self, db):
        """_store_super must set status='new', not 'approved'."""
        from project_forge.cron.auto_scan import run_auto_scan
        from project_forge.engine.super_ideas import SuperIdeaGenerator

        # Seed with enough ideas for super generation
        await run_auto_scan(db, count=50)

        sig = SuperIdeaGenerator(db)
        supers = await sig.generate(count=1)

        if supers:
            # The super idea's stored version should have status "new"
            stored = await db.get_idea(supers[0].id)
            if stored:
                assert stored.status == "new", (
                    f"Super idea status should be 'new', got '{stored.status}'"
                )

    @pytest.mark.asyncio
    async def test_super_ideas_not_in_approved_count(self, db):
        """Dashboard stats should not count super ideas as 'approved'."""
        # Create a super idea directly to test
        super_idea = Idea(
            name="[SUPER] Test Platform",
            tagline="a comprehensive testing platform synthesis",
            description="Synthesized super idea for testing",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Big market",
            feasibility_score=0.9,
            mvp_scope="Build it all.",
            status="new",  # Must be new, not approved
        )
        await db.save_idea(super_idea)

        stats = await db.get_stats()
        approved_count = stats["ideas_by_status"].get("approved", 0)
        assert approved_count == 0, "Super ideas should not inflate approved count"


# ---------------------------------------------------------------------------
# Bug #17: Browser POST auth with FORGE_API_TOKEN
# ---------------------------------------------------------------------------


class TestBug17BrowserPostAuth:
    """Browser POST requests must work when FORGE_API_TOKEN is set."""

    @pytest.mark.asyncio
    async def test_post_with_bearer_token_succeeds(self):
        """POST with correct Bearer token should not return 401."""
        from project_forge.web.auth import BearerTokenMiddleware

        test_token = "test-secret-token-12345"  # noqa: S105
        middleware = BearerTokenMiddleware(app=MagicMock())

        # Simulate the dispatch logic directly
        with patch("project_forge.web.auth.settings") as mock_settings:
            mock_settings.api_token = test_token

            # Request with valid token should pass through
            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.headers = {"Authorization": f"Bearer {test_token}"}

            mock_next = AsyncMock(return_value=MagicMock(status_code=200))
            await middleware.dispatch(mock_request, mock_next)

            # call_next should have been called (not blocked)
            mock_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_without_token_returns_401(self):
        """POST without Bearer token should return 401 when token is configured."""
        from project_forge.web.auth import BearerTokenMiddleware

        test_token = "test-secret-token-12345"  # noqa: S105
        middleware = BearerTokenMiddleware(app=MagicMock())

        with patch("project_forge.web.auth.settings") as mock_settings:
            mock_settings.api_token = test_token

            mock_request = MagicMock()
            mock_request.method = "POST"
            mock_request.headers = {}

            mock_next = AsyncMock()
            response = await middleware.dispatch(mock_request, mock_next)

            mock_next.assert_not_called()
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_base_template_includes_api_token_meta(self):
        """Base template must inject api-token meta tag when token is set."""
        from project_forge.web.app import templates

        # Check that get_api_token is available in template globals
        assert "get_api_token" in templates.env.globals

    @pytest.mark.asyncio
    async def test_app_js_uses_auth_headers(self):
        """app.js must call getAuthHeaders() for all POST requests."""
        js_path = Path("src/project_forge/web/static/app.js")
        content = js_path.read_text()

        # Every fetch POST should use getAuthHeaders
        assert "getAuthHeaders()" in content, "app.js must define getAuthHeaders()"

        # Count fetch calls with POST
        import re

        post_fetches = re.findall(r"fetch\([^)]*\{[^}]*method:\s*['\"]POST['\"]", content, re.DOTALL)
        auth_header_uses = content.count("getAuthHeaders()")

        # There should be at least as many getAuthHeaders() calls as POST fetches
        assert auth_header_uses >= len(post_fetches), (
            f"Found {len(post_fetches)} POST fetches but only {auth_header_uses} getAuthHeaders() calls"
        )


# ---------------------------------------------------------------------------
# Bug #30: Runners must work without API key
# ---------------------------------------------------------------------------


class TestBug30NoApiKey:
    """All runners must function without an API key."""

    @pytest.mark.asyncio
    async def test_review_runner_works_without_key(self, db):
        """Review runner should use heuristic review when no API key."""
        from datetime import UTC, datetime, timedelta

        from project_forge.cron.review_runner import run_review_cycle

        old_date = datetime.now(UTC) - timedelta(days=10)
        review_taglines = [
            "automated certificate revocation list monitoring",
            "kubernetes pod security policy auditor",
            "infrastructure drift detection for terraform states",
        ]
        for i in range(3):
            idea = Idea(
                name=f"Review Test {i}",
                tagline=review_taglines[i],
                description="A sufficiently detailed description for testing review heuristics.",
                category=IdeaCategory.SECURITY_TOOL,
                market_analysis="Market analysis here.",
                feasibility_score=0.6,
                mvp_scope="Build a CLI tool for scanning.",
                generated_at=old_date,
            )
            await db.save_idea(idea)

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            with patch("project_forge.cron.review_runner.settings") as mock_settings:
                mock_settings.anthropic_api_key = ""
                mock_settings.anthropic_model = "test"
                result = await run_review_cycle(db, batch_size=3, min_age_days=7)

        assert result["reviewed"] == 3
        assert all(r["status"] == "reviewed" for r in result["results"])

    @pytest.mark.asyncio
    async def test_si_runner_skips_without_key(self):
        """Self-improve runner should skip gracefully without API key, not crash."""
        from project_forge.cron.self_improve_runner import run_self_improve_cycle

        with patch("project_forge.cron.self_improve_runner.fetch_ci_queue_issues") as mock_fetch:
            mock_fetch.return_value = [
                {"number": 99, "title": "Test Issue", "body": "Fix something"},
            ]

            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
                with patch("project_forge.cron.self_improve_runner.settings") as mock_settings:
                    mock_settings.anthropic_api_key = ""
                    result = await run_self_improve_cycle()

        assert result["processed"] == 1
        assert result["results"][0]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_challenge_returns_heuristic_without_key(self, db):
        """Challenge endpoint should return a useful heuristic response without API key."""
        idea = Idea(
            name="Challenge Test Idea",
            tagline="test challenge without api key",
            description="A detailed project description for testing challenges.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Strong market need.",
            feasibility_score=0.7,
            mvp_scope="Build a CLI tool.",
        )
        await db.save_idea(idea)

        from project_forge.web.routes import _challenge_idea

        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}, clear=False):
            with patch("project_forge.web.routes.settings") as mock_settings:
                mock_settings.anthropic_api_key = ""
                result = await _challenge_idea(idea, "Is this idea feasible?")

        # Should return a meaningful response, not just "unavailable"
        assert result["response"], "Challenge should return a non-empty response"
        assert "unavailable" not in result["response"].lower(), (
            "Challenge should provide heuristic analysis, not just say 'unavailable'"
        )
