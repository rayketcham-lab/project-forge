"""TDD: Multi-step idea builder wizard.

User-requested: instead of a one-shot expansion that produces a generic
"build a PKI" idea, a 5-phase wizard that asks intelligent follow-ups
to draw the idea out of the user.

Phases:
  1. Discover     — clarify core problem (who hits it, when, frequency)
  2. Differentiate — what's missing in existing tools, unique angle
  3. Audience     — concrete persona, buyer, decision-maker
  4. Constraints  — tech stack, must-haves, deployment model
  5. Synthesize   — produce the structured Idea (name, tagline, scope, etc.)

Backend is stateless. Frontend accumulates state between steps and
POSTs the full state on each step. Step 1-4 return follow-up questions;
step 5 returns a draft Idea.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# ── Prompt builder produces step-aware prompts ──────────────────────


class TestBuilderPrompt:
    def test_step1_asks_about_core_problem(self):
        from project_forge.engine.idea_builder import build_step_prompt

        p = build_step_prompt(
            step=1,
            fragment="A SBOM diff tool.",
            answers=[],
            category_hint=None,
        )
        # Step 1 prompt focuses on the core problem
        assert "1" in p or "Discover" in p or "core problem" in p.lower()
        assert "A SBOM diff tool" in p

    def test_step2_includes_step1_answers(self):
        from project_forge.engine.idea_builder import build_step_prompt

        answers = [{"question": "Who hits this?", "answer": "Security engineers."}]
        p = build_step_prompt(
            step=2,
            fragment="A SBOM diff tool.",
            answers=answers,
            category_hint=None,
        )
        assert "Security engineers" in p

    def test_step5_requests_final_draft_json(self):
        from project_forge.engine.idea_builder import build_step_prompt

        p = build_step_prompt(
            step=5,
            fragment="A SBOM diff tool.",
            answers=[
                {"question": "Who?", "answer": "Sec engineers"},
                {"question": "Diff?", "answer": "tree compare"},
            ],
            category_hint="security-tool",
        )
        # Step 5 must request the full Idea draft fields
        for field in ("name", "tagline", "description", "feasibility_score", "mvp_scope"):
            assert field in p

    def test_steps_1_to_4_request_questions_array(self):
        from project_forge.engine.idea_builder import build_step_prompt

        for step in (1, 2, 3, 4):
            p = build_step_prompt(
                step=step,
                fragment="x",
                answers=[],
                category_hint=None,
            )
            assert "questions" in p.lower()

    def test_invalid_step_raises(self):
        from project_forge.engine.idea_builder import build_step_prompt

        with pytest.raises(ValueError):
            build_step_prompt(step=0, fragment="x", answers=[], category_hint=None)
        with pytest.raises(ValueError):
            build_step_prompt(step=6, fragment="x", answers=[], category_hint=None)


# ── Endpoint state machine ──────────────────────────────────────────


@pytest_asyncio.fixture
async def client(tmp_path):
    from project_forge.web.app import app, db

    db.db_path = tmp_path / "wiz.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.close()


@pytest.mark.asyncio
async def test_step1_returns_questions(client, monkeypatch):
    """Step 1 with a fresh fragment must return follow-up questions."""

    def fake_call(prompt: str) -> str:  # noqa: ARG001
        return '{"questions": ["Who hits this most?", "How often?"]}'

    with patch(
        "project_forge.engine.idea_builder.resolve_backend",
        return_value=type("B", (), {"call": staticmethod(fake_call), "name": "stub"})(),
    ):
        resp = await client.post(
            "/api/ideas/builder/step",
            json={"step": 1, "fragment": "A SBOM diff tool.", "answers": []},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "questions" in data
    assert len(data["questions"]) >= 1


@pytest.mark.asyncio
async def test_step5_returns_draft_idea(client, monkeypatch):
    """Step 5 with full answers must return a draft Idea (NOT save it yet)."""
    draft_json = (
        '{"draft": {"name": "SBOM Drift", "tagline": "Catch transitive churn",'
        ' "description": "d", "category": "security-tool",'
        ' "market_analysis": "m", "feasibility_score": 0.8,'
        ' "mvp_scope": "s", "tech_stack": ["python"]}}'
    )

    def fake_call(prompt: str) -> str:  # noqa: ARG001
        return draft_json

    with patch(
        "project_forge.engine.idea_builder.resolve_backend",
        return_value=type("B", (), {"call": staticmethod(fake_call), "name": "stub"})(),
    ):
        resp = await client.post(
            "/api/ideas/builder/step",
            json={
                "step": 5,
                "fragment": "A SBOM diff tool.",
                "answers": [
                    {"question": "Who?", "answer": "Sec engineers."},
                    {"question": "Why?", "answer": "Hard to spot transitive."},
                    {"question": "Tech?", "answer": "Python."},
                    {"question": "Scope?", "answer": "CLI + diff."},
                ],
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "draft" in data
    assert data["draft"]["name"] == "SBOM Drift"
    # Step 5 returns the draft but does NOT save (save is a separate endpoint)


@pytest.mark.asyncio
async def test_save_persists_idea(client):
    """POST /api/ideas/builder/save persists the finalized draft."""
    payload = {
        "name": "SBOM Drift",
        "tagline": "Catch transitive churn between merges",
        "description": "Real description.",
        "category": "security-tool",
        "market_analysis": "Market.",
        "feasibility_score": 0.8,
        "mvp_scope": "CLI tool.",
        "tech_stack": ["python", "click"],
    }
    resp = await client.post("/api/ideas/builder/save", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Either accepted (id present) or filtered as duplicate
    assert "id" in data or data.get("filtered")


@pytest.mark.asyncio
async def test_step_with_invalid_step_returns_422(client):
    resp = await client.post(
        "/api/ideas/builder/step",
        json={"step": 99, "fragment": "x", "answers": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_step_with_empty_fragment_returns_422(client):
    resp = await client.post(
        "/api/ideas/builder/step",
        json={"step": 1, "fragment": "  ", "answers": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_backend_returns_helpful_error(client, monkeypatch):
    """When no LLM backend is available, the wizard returns a clear
    message rather than crashing."""
    with patch(
        "project_forge.engine.idea_builder.resolve_backend",
        return_value=None,
    ):
        resp = await client.post(
            "/api/ideas/builder/step",
            json={"step": 1, "fragment": "A SBOM tool.", "answers": []},
        )
    # 503 (service unavailable) is the right code for "LLM unreachable"
    assert resp.status_code == 503
    assert "LLM" in resp.text or "backend" in resp.text.lower()


# ── Step-by-step JSON parsing handles bad LLM output ────────────────


class TestParseStepResponse:
    def test_valid_questions_json(self):
        from project_forge.engine.idea_builder import parse_step_response

        result = parse_step_response('{"questions": ["A?", "B?"]}', step=1)
        assert result["questions"] == ["A?", "B?"]

    def test_valid_draft_json(self):
        from project_forge.engine.idea_builder import parse_step_response

        raw = (
            '{"draft": {"name": "X", "tagline": "t", "description": "d",'
            ' "category": "security-tool", "market_analysis": "m",'
            ' "feasibility_score": 0.8, "mvp_scope": "s", "tech_stack": []}}'
        )
        result = parse_step_response(raw, step=5)
        assert result["draft"]["name"] == "X"

    def test_garbage_returns_error_field(self):
        from project_forge.engine.idea_builder import parse_step_response

        result = parse_step_response("not json", step=1)
        assert "error" in result

    def test_strips_markdown_fence(self):
        from project_forge.engine.idea_builder import parse_step_response

        wrapped = '```json\n{"questions": ["q1", "q2"]}\n```'
        result = parse_step_response(wrapped, step=2)
        assert result["questions"] == ["q1", "q2"]
