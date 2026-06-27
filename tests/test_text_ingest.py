"""TDD: Add Idea from Text — expand a fragment into a structured project idea.

Companion to URL-ingest. User types/pastes any fragment (a half-formed
thought, a research question, a frustration, a code snippet) and the
LLM expands it into a full Idea. Falls back to heuristic extraction
when no LLM backend is available.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Pydantic request validation ──────────────────────────────────────


class TestTextIngestRequest:
    def test_minimal_text_accepted(self):
        from project_forge.models import TextIngestRequest

        r = TextIngestRequest(text="A tool that detects supply chain attacks at build time.")
        assert r.text.startswith("A tool")
        assert r.category is None

    def test_empty_text_rejected(self):
        from project_forge.models import TextIngestRequest

        with pytest.raises(ValueError):
            TextIngestRequest(text="")

    def test_whitespace_only_rejected(self):
        from project_forge.models import TextIngestRequest

        with pytest.raises(ValueError):
            TextIngestRequest(text="   \n  \t ")

    def test_category_optional(self):
        from project_forge.models import TextIngestRequest

        r = TextIngestRequest(text="x" * 50, category="security-tool")
        assert r.category == "security-tool"


# ── Prompt builder ───────────────────────────────────────────────────


class TestTextIngestPrompt:
    def test_includes_user_text_verbatim(self):
        from project_forge.engine.prompts import build_text_ingest_prompt

        text = "A SBOM diff tool that flags transitive dependency churn before merge."
        prompt = build_text_ingest_prompt(text=text)
        assert text in prompt

    def test_category_hint_appears_in_prompt(self):
        from project_forge.engine.prompts import build_text_ingest_prompt

        prompt = build_text_ingest_prompt(text="x" * 100, category_hint="security-tool")
        assert "security-tool" in prompt

    def test_no_hint_lists_categories(self):
        from project_forge.engine.prompts import build_text_ingest_prompt

        prompt = build_text_ingest_prompt(text="x" * 100, category_hint=None)
        # Without a hint, the prompt should expose the category options
        assert "security-tool" in prompt or "Choose" in prompt

    def test_prompt_requests_json_format(self):
        from project_forge.engine.prompts import build_text_ingest_prompt

        prompt = build_text_ingest_prompt(text="x" * 100)
        assert "JSON" in prompt or "json" in prompt
        for field in ("name", "tagline", "description", "category", "feasibility_score", "mvp_scope"):
            assert field in prompt, f"Prompt missing required JSON field: {field}"


# ── Heuristic fallback (no LLM) ──────────────────────────────────────


class TestHeuristicFallback:
    def test_returns_idea_with_text_in_description(self):
        from project_forge.engine.text_ingest import _heuristic_idea_from_text

        text = "Generate a tool for static analysis of Python type hints."
        idea = _heuristic_idea_from_text(text=text, category_hint=None)
        # The full text must be preserved somewhere — user expects to see
        # their original input reflected in the saved idea.
        assert text in idea.description

    def test_uses_category_hint_when_provided(self):
        from project_forge.engine.text_ingest import _heuristic_idea_from_text
        from project_forge.models import IdeaCategory

        idea = _heuristic_idea_from_text(text="x" * 50, category_hint="privacy")
        assert idea.category == IdeaCategory.PRIVACY

    def test_unknown_category_falls_back_to_security(self):
        from project_forge.engine.text_ingest import _heuristic_idea_from_text
        from project_forge.models import IdeaCategory

        idea = _heuristic_idea_from_text(text="x" * 50, category_hint="not-a-category")
        assert idea.category == IdeaCategory.SECURITY_TOOL


# ── End-to-end: POST /api/ideas/from-text ────────────────────────────


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "text.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


@pytest.mark.asyncio
async def test_post_text_ingest_returns_idea(client, monkeypatch):
    """End-to-end: POST text fragment, get back a saved Idea."""
    # Stub the LLM backend so the test runs offline.
    from project_forge.models import Idea, IdeaCategory

    async def fake_generate(text, category_hint=None):  # noqa: ARG001
        return Idea(
            name="Stub Idea",
            tagline="Generated from user text",
            description="A stub idea built from the user's text fragment.",
            category=IdeaCategory.SECURITY_TOOL,
            market_analysis="Stub market.",
            feasibility_score=0.75,
            mvp_scope="Stub scope.",
            tech_stack=["python"],
        )

    monkeypatch.setattr(
        "project_forge.web.routes.generate_idea_from_text",
        fake_generate,
    )

    resp = await client.post(
        "/api/ideas/from-text",
        json={"text": "A tool that diff'es SBOM trees and flags churn pre-merge."},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("name") == "Stub Idea"
    assert "id" in data


@pytest.mark.asyncio
async def test_post_text_ingest_rejects_empty(client):
    resp = await client.post(
        "/api/ideas/from-text",
        json={"text": "   "},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_text_ingest_uses_category_hint(client, monkeypatch):
    from project_forge.models import Idea, IdeaCategory

    captured = {}

    async def fake_generate(text, category_hint=None):
        captured["text"] = text
        captured["category_hint"] = category_hint
        return Idea(
            name="X",
            tagline="t",
            description="d",
            category=IdeaCategory.PRIVACY,
            market_analysis="m",
            feasibility_score=0.7,
            mvp_scope="mvp",
            tech_stack=["python"],
        )

    monkeypatch.setattr("project_forge.web.routes.generate_idea_from_text", fake_generate)

    await client.post(
        "/api/ideas/from-text",
        json={"text": "Idea about user data privacy.", "category": "privacy"},
    )
    assert captured["category_hint"] == "privacy"
    assert "privacy" in captured["text"].lower()


# ── Backend resolver integration (no API key) ────────────────────────


@pytest.mark.asyncio
async def test_falls_back_to_heuristic_when_no_backend(monkeypatch):
    """generate_idea_from_text must produce an Idea even without LLM."""
    from project_forge.engine.text_ingest import generate_idea_from_text

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from project_forge.config import settings as _settings

    monkeypatch.setattr(_settings, "anthropic_api_key", "")
    with patch(
        "project_forge.engine.llm_backend._has_claude_cli",
        return_value=False,
    ):
        idea = await generate_idea_from_text(text="A privacy auditor tool.", category_hint=None)

    assert idea is not None
    assert "privacy auditor tool" in idea.description.lower() or "privacy auditor tool" in idea.name.lower()
