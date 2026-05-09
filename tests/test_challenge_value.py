"""TDD: challenge flow must produce value, not just a DB row (#70).

Three contracts:
1. _challenge_idea uses the LLM backend resolver — works with Claude Code
   CLI when no ANTHROPIC_API_KEY is set, instead of falling through to a
   heuristic stub that ignores the user's question.
2. POST /api/ideas/{id}/challenges/{cid}/apply reads the challenge's
   `changes` array and updates the idea fields accordingly.
3. The apply endpoint is idempotent: a second call on the same
   challenge is a no-op.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory


def _stub_idea() -> Idea:
    return Idea(
        name="Test Idea",
        tagline="t",
        description="Initial description.",
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="Initial market.",
        feasibility_score=0.7,
        mvp_scope="Initial scope.",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    """App + DB + LLM-backend free of API key (forces resolver to try
    Claude Code path, which we then mock)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from project_forge.config import settings as _settings
    monkeypatch.setattr(_settings, "anthropic_api_key", "")

    from project_forge.web.app import app, db

    db.db_path = tmp_path / "challenge_value.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


# ── 1. _challenge_idea uses LLM backend resolver ──────────────────


@pytest.mark.asyncio
async def test_challenge_uses_llm_backend_when_no_api_key():
    """When no ANTHROPIC_API_KEY but claude CLI is available, the
    challenge handler must route through the backend resolver — NOT
    fall back to the heuristic stub.
    """
    from project_forge.web import routes

    fake_response = (
        '{"response": "Real LLM analysis of the question — '
        'specifically pushing back on feasibility because the threat model '
        'is unclear.", "verdict": "narrow", "confidence": 0.78, '
        '"changes": [{"field": "mvp_scope", "action": "modified", '
        '"text": "Add explicit threat-model section"}]}'
    )

    class _FakeBackend:
        name = "test-backend"
        def call(self, prompt: str) -> str:  # noqa: ARG002
            return fake_response

    with patch(
        "project_forge.web.routes.resolve_backend",
        return_value=_FakeBackend(),
    ):
        result = await routes._challenge_idea(
            _stub_idea(),
            question="What's the threat model here?",
            challenge_type="feasibility",
            focus_area="all",
            tone="skeptical",
        )

    # The LLM-derived response, NOT the heuristic stub
    assert "heuristic analysis" not in result["response"], (
        f"Got heuristic stub, not LLM response: {result['response'][:200]}"
    )
    assert "threat model" in result["response"].lower()
    assert result["verdict"] == "narrow"
    assert result["confidence"] == pytest.approx(0.78, abs=0.01)
    assert len(result["changes"]) == 1
    assert result["changes"][0]["field"] == "mvp_scope"


# ── 2. Apply-changes endpoint ─────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_changes_updates_idea(client):
    """POST /api/ideas/{id}/challenges/{cid}/apply must update the
    idea fields per the challenge's changes array."""
    from project_forge.models import Challenge
    from project_forge.web.app import db

    idea = _stub_idea()
    await db.save_idea(idea)
    ch = Challenge(
        idea_id=idea.id,
        question="?",
        response="LLM said do this",
        verdict="narrow",
        confidence=0.8,
        changes=[
            {"field": "mvp_scope", "action": "modified",
             "text": "New tighter scope: just the threat-model section."},
            {"field": "feasibility_score", "action": "modified",
             "text": "0.82"},
        ],
    )
    await db.save_challenge(ch)

    resp = await client.post(f"/api/ideas/{idea.id}/challenges/{ch.id}/apply")
    assert resp.status_code == 200, resp.text

    # Re-read the idea — fields must be updated
    fresh = await db.get_idea(idea.id)
    assert "threat-model section" in fresh.mvp_scope
    assert fresh.feasibility_score == pytest.approx(0.82, abs=0.01)


@pytest.mark.asyncio
async def test_apply_changes_is_idempotent(client):
    """Calling apply twice on the same challenge must not double-mutate."""
    from project_forge.models import Challenge
    from project_forge.web.app import db

    idea = _stub_idea()
    await db.save_idea(idea)
    ch = Challenge(
        idea_id=idea.id,
        question="?",
        response="r",
        verdict="narrow",
        confidence=0.8,
        changes=[{"field": "mvp_scope", "action": "modified", "text": "v2"}],
    )
    await db.save_challenge(ch)

    await client.post(f"/api/ideas/{idea.id}/challenges/{ch.id}/apply")
    second = await client.post(f"/api/ideas/{idea.id}/challenges/{ch.id}/apply")

    # Second call must be 200 with already_applied=True (or similar);
    # MUST NOT mutate again.
    assert second.status_code == 200
    body = second.json()
    assert body.get("already_applied") is True or body.get("changed") is False


@pytest.mark.asyncio
async def test_apply_returns_404_for_missing_challenge(client):
    from project_forge.web.app import db

    idea = _stub_idea()
    await db.save_idea(idea)

    resp = await client.post(
        f"/api/ideas/{idea.id}/challenges/nonexistent/apply",
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_returns_404_for_wrong_idea(client):
    """Challenge belongs to a different idea — apply must reject."""
    from project_forge.models import Challenge
    from project_forge.web.app import db

    idea1 = _stub_idea()
    idea2 = _stub_idea()
    await db.save_idea(idea1)
    await db.save_idea(idea2)
    ch = Challenge(
        idea_id=idea1.id, question="?", response="r",
        verdict="narrow", confidence=0.8,
        changes=[{"field": "mvp_scope", "action": "modified", "text": "v"}],
    )
    await db.save_challenge(ch)

    resp = await client.post(f"/api/ideas/{idea2.id}/challenges/{ch.id}/apply")
    assert resp.status_code == 404
