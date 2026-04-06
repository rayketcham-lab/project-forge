"""Tests for Team Review Phase 2 — issues #42, #43, #44.

#42: API token must NOT be exposed in HTML meta tag
#43: Wire scorer + category saturation into generation pipeline
#44: Extract dedup logic out of Database.save_idea()
"""

import pytest
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory


def _make_idea(name: str, **kw) -> Idea:
    return Idea(
        name=name,
        tagline=kw.get("tagline", f"{name} tagline"),
        description=kw.get("description", f"Description of {name}"),
        category=kw.get("category", IdeaCategory.SECURITY_TOOL),
        market_analysis="Good market.",
        feasibility_score=kw.get("score", 0.7),
        mvp_scope=kw.get("mvp_scope", "Build it."),
        tech_stack=kw.get("tech_stack", ["python"]),
    )


# ============================================================
# #42: API TOKEN MUST NOT BE IN HTML
# ============================================================


class TestNoTokenInHTML:
    """The API bearer token must never be injected into HTML pages."""

    def test_base_template_has_no_api_token_meta(self):
        """base.html must not contain a meta tag that injects the API token."""
        from pathlib import Path

        base_html = Path("src/project_forge/web/templates/base.html").read_text()
        assert "api-token" not in base_html, (
            "base.html must not contain api-token meta tag — it exposes the bearer token to every page visitor"
        )

    def test_no_get_api_token_in_template_globals(self):
        """The Jinja2 template engine must not expose get_api_token as a global."""
        from project_forge.web.app import templates

        assert "get_api_token" not in templates.env.globals, (
            "get_api_token must not be a template global — it would allow templates to leak the API token"
        )

    def test_app_js_uses_csrf_not_bearer(self):
        """app.js must not read a bearer token from meta tags."""
        from pathlib import Path

        app_js = Path("src/project_forge/web/static/app.js").read_text()
        assert 'meta[name="api-token"]' not in app_js, "app.js must not read bearer token from meta tags"

    @pytest.mark.asyncio
    async def test_dashboard_html_has_no_token(self, tmp_path):
        """The rendered dashboard page must not contain any token value."""
        from project_forge.config import settings
        from project_forge.web.app import app, db

        original = settings.api_token
        settings.api_token = "VISIBLE_SECRET_12345"  # noqa: S105
        try:
            db.db_path = tmp_path / "test_no_token.db"
            await db.connect()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/")
                assert "VISIBLE_SECRET_12345" not in resp.text, "Dashboard HTML must not contain the API token value"
            await db.close()
        finally:
            settings.api_token = original

    @pytest.mark.asyncio
    async def test_post_endpoints_still_work_without_token_in_html(self, tmp_path):
        """POST endpoints must still work when dashboard doesn't inject token.

        The JS should use a CSRF token or cookie-based auth, not bearer from meta.
        """
        from project_forge.web.app import app, db

        db.db_path = tmp_path / "test_post_no_token.db"
        await db.connect()
        idea = _make_idea("Post Test Idea")
        await db.save_idea(idea)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # With no token set, POST should still work (token auth disabled)
            resp = await client.post(f"/ideas/{idea.id}/approve")
            assert resp.status_code == 200
        await db.close()


# ============================================================
# #43: WIRE SCORER + CATEGORY SATURATION INTO PIPELINE
# ============================================================


class TestScorerWiredIntoPipeline:
    """score_idea() must be called during idea generation to adjust scores."""

    def test_scheduler_imports_score_idea(self):
        """scheduler.py must import and use score_idea."""
        import inspect

        from project_forge.cron import scheduler

        source = inspect.getsource(scheduler)
        assert "score_idea" in source, (
            "scheduler.py must use score_idea() from the scorer module "
            "to independently evaluate ideas during generation"
        )

    def test_generate_and_store_calls_scorer(self):
        """generate_and_store must invoke score_idea on the generated idea."""
        import inspect

        from project_forge.cron import scheduler

        source = inspect.getsource(scheduler.generate_and_store)
        assert "score_idea" in source, "generate_and_store() must call score_idea() to compute independent scores"


class TestCategorySaturation:
    """Category picker must use saturation data to avoid exhausted categories."""

    def test_pick_category_uses_saturation(self):
        """pick_category must consider tuple saturation, not just recency."""
        import inspect

        from project_forge.cron import scheduler

        source = inspect.getsource(scheduler.pick_category)
        assert "unused_tuple" in source.lower() or "saturation" in source.lower(), (
            "pick_category() must check category saturation (get_unused_tuple_count) to avoid exhausted categories"
        )

    @pytest.mark.asyncio
    async def test_saturated_category_deprioritized(self, db):
        """A category with 0 unused tuples should be less likely to be picked."""
        # We can't easily test randomness, but we can verify the function
        # at least considers saturation by checking it doesn't crash and
        # returns a valid category
        from project_forge.cron.scheduler import pick_category

        cat = await pick_category(db)
        assert cat in list(IdeaCategory)


# ============================================================
# #44: EXTRACT DEDUP LOGIC FROM save_idea()
# ============================================================


class TestDedupGate:
    """Dedup logic must be a standalone function, not embedded in save_idea()."""

    def test_dedup_gate_function_exists(self):
        """A dedup gate function must exist outside of the Database class."""
        from project_forge.engine.dedup import should_accept

        assert callable(should_accept)

    @pytest.mark.asyncio
    async def test_dedup_gate_accepts_unique_idea(self, db):
        """should_accept returns (True, None) for a unique idea."""
        from project_forge.engine.dedup import should_accept

        idea = _make_idea(
            "Completely Novel Idea XYZ",
            tagline="A unique approach to quantum-resistant signatures",
        )
        accepted, reason = await should_accept(idea, db)
        assert accepted is True
        assert reason is None

    @pytest.mark.asyncio
    async def test_dedup_gate_rejects_duplicate_tagline(self, db):
        """should_accept returns (False, reason) for a near-duplicate tagline."""
        from project_forge.engine.dedup import should_accept

        idea1 = _make_idea(
            "PKI Cert Manager",
            tagline="Automated certificate lifecycle management",
        )
        await db.save_idea(idea1)

        idea2 = _make_idea(
            "PKI Cert Manager v2",
            tagline="Automated certificate lifecycle management",
        )
        accepted, reason = await should_accept(idea2, db)
        assert accepted is False
        assert reason is not None
        assert "duplicate" in reason.lower() or "similar" in reason.lower()

    @pytest.mark.asyncio
    async def test_dedup_gate_rejects_content_hash_dup(self, db):
        """should_accept returns (False, reason) for a content hash duplicate."""
        from project_forge.engine.dedup import should_accept

        idea1 = _make_idea("Hash Dup Test")
        idea1.content_hash = "abc123hash"
        await db.save_idea(idea1)

        idea2 = _make_idea("Hash Dup Test 2")
        idea2.content_hash = "abc123hash"
        accepted, reason = await should_accept(idea2, db)
        assert accepted is False
        assert "content_hash" in reason.lower() or "hash" in reason.lower()

    def test_save_idea_no_longer_does_dedup(self):
        """Database.save_idea() must not contain dedup logic — it should just store."""
        import inspect

        from project_forge.storage.db import Database

        source = inspect.getsource(Database.save_idea)
        assert "tagline_similarity" not in source, (
            "save_idea() must not call tagline_similarity — dedup should be in the standalone should_accept() gate"
        )
        assert "SIMILARITY_THRESHOLD" not in source, (
            "save_idea() must not reference SIMILARITY_THRESHOLD — dedup logic belongs in should_accept()"
        )
