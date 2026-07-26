"""TDD: Backend must always return JSON errors, never plain-text HTML.

Bug: When any endpoint throws an unhandled exception, FastAPI returns
plain-text "Internal Server Error". JS calls resp.json() on that body
and gets "Unexpected token 'I'..." SyntaxError.

Fix:
1. Global exception handler on app that wraps all 500s in {"detail": "..."}
2. app.js safeJson() helper to defensively parse any error body
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_json_err.db"
    await db.connect()
    # raise_app_exceptions=False: Starlette's ServerErrorMiddleware re-raises after
    # sending the response so servers can log it. In tests we want the HTTP response,
    # not the re-raised exception.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


class TestGlobalExceptionHandlerReturnsJSON:
    """Unhandled exceptions must return JSON, not HTML 'Internal Server Error'."""

    @pytest.mark.asyncio
    async def test_url_ingest_500_returns_json(self, client):
        """When URL ingest throws, the response body must be valid JSON."""
        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new_callable=AsyncMock,
            side_effect=RuntimeError("downstream API unavailable"),
        ):
            resp = await client.post(
                "/api/ideas/from-url",
                json={"url": "https://example.com/article"},
            )
        assert resp.status_code == 500
        # Must parse as JSON — not raise SyntaxError
        body = resp.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_500_content_type_is_json(self, client):
        """Content-Type header must be application/json on error responses."""
        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new_callable=AsyncMock,
            side_effect=ValueError("unexpected error"),
        ):
            resp = await client.post(
                "/api/ideas/from-url",
                json={"url": "https://example.com/article"},
            )
        assert resp.status_code == 500
        assert "application/json" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_404_still_returns_json(self, client):
        """404 responses from FastAPI must also be JSON."""
        resp = await client.get("/api/ideas/nonexistent-id-xyz/approve")
        # FastAPI default 404/422 are already JSON — verify not broken
        assert resp.status_code in (404, 405, 422)
        body = resp.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_challenge_500_returns_json(self, client):
        """Challenge endpoint 500 must return JSON, not HTML."""
        from project_forge.models import Idea, IdeaCategory

        idea = Idea(
            name="Test idea",
            tagline="A test",
            description="Test.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Big.",
            feasibility_score=0.8,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(idea)

        with patch(
            "project_forge.web.routes._challenge_idea",
            new_callable=AsyncMock,
            side_effect=RuntimeError("API down"),
        ):
            resp = await client.post(
                f"/api/ideas/{idea.id}/challenge",
                json={
                    "question": "Is this feasible?",
                    "challenge_type": "feasibility",
                },
            )
        assert resp.status_code == 500
        body = resp.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_compare_500_returns_json(self, client):
        """Compare endpoint 500 must return JSON."""
        from project_forge.models import Idea, IdeaCategory

        idea = Idea(
            name="Compare idea",
            tagline="A test",
            description="Test.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Big.",
            feasibility_score=0.8,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(idea)

        # compare_idea_to_repo is imported locally inside the route; mock at source module
        with patch(
            "project_forge.engine.compare.compare_idea_to_repo",
            side_effect=RuntimeError("GitHub unreachable"),
        ):
            resp = await client.post(
                f"/api/ideas/{idea.id}/compare?repo=some-repo",
            )
        # 502 (HTTPException from repo fetch) or 500 (unhandled) — both must be JSON
        assert resp.status_code in (500, 502)
        body = resp.json()
        assert "detail" in body

    @pytest.mark.asyncio
    async def test_add_to_project_500_returns_json(self, client):
        """Add-to-project endpoint 500 must return JSON."""
        from project_forge.models import Idea, IdeaCategory

        idea = Idea(
            name="Project idea",
            tagline="A test",
            description="Test.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Big.",
            feasibility_score=0.8,
            mvp_scope="MVP.",
            tech_stack=["python"],
        )
        await db.save_idea(idea)

        # create_issue is imported locally inside the route; mock at source module
        with patch(
            "project_forge.scaffold.github.create_issue",
            side_effect=RuntimeError("GitHub API 503"),
        ):
            resp = await client.post(
                f"/api/ideas/{idea.id}/add-to-project?repo=some-repo",
            )
        assert resp.status_code in (500, 502)
        body = resp.json()
        assert "detail" in body


class TestGlobalExceptionHandlerDoesNotLeakDetail:
    """The 500 body must be a fixed generic string — never the exception text.

    str(exc) can carry SQLite column/table names, filesystem paths, and library
    internals. The full detail belongs in the server log, not in the response.
    """

    LEAK = "no such column: ideas.secret_internal_column /srv/forge/data/forge.db"

    @pytest.mark.asyncio
    async def test_post_500_body_is_generic(self, client):
        """A POST 500 must return exactly the generic detail, not str(exc)."""
        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new_callable=AsyncMock,
            side_effect=RuntimeError(self.LEAK),
        ):
            resp = await client.post(
                "/api/ideas/from-url",
                json={"url": "https://example.com/article"},
            )
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}
        assert self.LEAK not in resp.text

    @pytest.mark.asyncio
    async def test_unauthenticated_get_500_body_is_generic(self, client):
        """GET is exempt from bearer auth — its 500s must not leak internals either."""
        with patch(
            "project_forge.engine.scoreboard.build_calibration",
            new_callable=AsyncMock,
            side_effect=RuntimeError(self.LEAK),
        ):
            resp = await client.get("/api/scoreboard")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}
        assert "secret_internal_column" not in resp.text

    @pytest.mark.asyncio
    async def test_empty_exception_message_still_generic(self, client):
        """An exception with no message must still produce the generic detail."""
        with patch(
            "project_forge.web.routes.ingest_idea_from_url",
            new_callable=AsyncMock,
            side_effect=RuntimeError(),
        ):
            resp = await client.post(
                "/api/ideas/from-url",
                json={"url": "https://example.com/article"},
            )
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error"}

    @pytest.mark.asyncio
    async def test_handler_still_logs_full_detail(self, client, caplog):
        """logger.exception must keep the real error server-side for debugging."""
        import logging

        with caplog.at_level(logging.ERROR, logger="project_forge.web.app"):
            with patch(
                "project_forge.web.routes.ingest_idea_from_url",
                new_callable=AsyncMock,
                side_effect=RuntimeError(self.LEAK),
            ):
                resp = await client.post(
                    "/api/ideas/from-url",
                    json={"url": "https://example.com/article"},
                )
        assert resp.status_code == 500
        assert self.LEAK in caplog.text

    @pytest.mark.asyncio
    async def test_http_exception_detail_still_passes_through(self, client):
        """Deliberate HTTPException details are still surfaced — only 500s are masked."""
        resp = await client.post("/api/ideas/no-such-idea-xyz/challenge", json={"question": "?"})
        assert resp.status_code in (404, 422)
        assert resp.json()["detail"] != "Internal server error"


class TestAppJSSafeJsonHelper:
    """app.js must define safeJson() and use it at all error-path .json() calls."""

    def test_safe_json_function_defined(self):
        """app.js must define a safeJson helper function."""
        import os

        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "project_forge",
            "web",
            "static",
            "app.js",
        )
        with open(js_path) as f:
            js = f.read()
        assert "function safeJson" in js or "safeJson" in js, "app.js must define safeJson() helper"

    def test_no_bare_json_on_error_paths(self):
        """Error paths that open a block on !ok must use safeJson(), not bare .json()."""
        import os

        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "project_forge",
            "web",
            "static",
            "app.js",
        )
        with open(js_path) as f:
            lines = f.readlines()

        # Detect: inside an `if (!resp.ok) {` (or !r.ok, !response.ok) block,
        # a bare .json() call is used instead of safeJson().
        # Inline-throw patterns (`if (!resp.ok) throw ...`) are intentionally excluded
        # because the .json() that follows is on the guaranteed-success path.
        violations = []
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if "await resp.json()" in stripped and "safeJson" not in stripped:
                # Only flag when inside a block-style error guard (has opening brace)
                context = "".join(lines[max(0, i - 4) : i]).lower()
                is_block_error = ("!resp.ok) {" in context or "!response.ok) {" in context) and "throw" not in context
                if is_block_error:
                    violations.append(f"Line {i}: {stripped}")
            if "await r.json()" in stripped and "safeJson" not in stripped:
                context = "".join(lines[max(0, i - 4) : i]).lower()
                if "!r.ok) {" in context and "throw" not in context:
                    violations.append(f"Line {i}: {stripped}")

        assert violations == [], (
            "Bare .json() calls inside !ok error blocks found — use safeJson() instead:\n" + "\n".join(violations)
        )

    def test_promote_proposal_checks_ok_before_json(self):
        """promoteProposal must check resp.ok before calling .json()."""
        import os

        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "project_forge",
            "web",
            "static",
            "app.js",
        )
        with open(js_path) as f:
            js = f.read()

        # Find the promoteProposal function body
        start = js.find("async function promoteProposal")
        end = js.find("\nasync function ", start + 1)
        if end == -1:
            end = start + 2000
        fn_body = js[start:end]

        # Must have .ok check before using the response data
        assert "r.ok" in fn_body or "resp.ok" in fn_body or "safeJson" in fn_body, (
            "promoteProposal must check response.ok or use safeJson before reading response body"
        )

    def test_reject_proposal_checks_ok_before_json(self):
        """rejectProposal must check resp.ok before calling .json()."""
        import os

        js_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "src",
            "project_forge",
            "web",
            "static",
            "app.js",
        )
        with open(js_path) as f:
            js = f.read()

        start = js.find("async function rejectProposal")
        end = js.find("\nasync function ", start + 1)
        if end == -1:
            end = start + 2000
        fn_body = js[start:end]

        assert "r.ok" in fn_body or "resp.ok" in fn_body or "safeJson" in fn_body, (
            "rejectProposal must check response.ok or use safeJson before reading response body"
        )
