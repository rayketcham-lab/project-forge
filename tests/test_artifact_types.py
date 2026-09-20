"""Tests for the v0.15a artifact-type expansion of /api/churn.

Mode is the *thinking lens* (novel/inversion/bundle/microservice/
adversarial); artifact_type is the *shape* of the output the LLM is
asked to produce — skill, sub-agent, MCP server, hook, slash-command,
workflow, protocol, or raw ability extension. Orthogonal axes.

No category auto-rotates artifact types today (the rotation set is
empty), so a category that does not specify one gets the default
project-pitch shape (artifact_type = None). An explicit artifact_type is
still honoured when a caller passes one.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from project_forge.engine.llm_generator import (
    _ARTIFACT_PROMPTS,
    ARTIFACT_TYPES,
    _build_prompt,
    generate_idea_llm,
    pick_least_used_artifact,
)
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

_OK_PAYLOAD = {
    "name": "Reproducibility Ledger",
    "tagline": "log every sub-agent run with verified inputs and outputs",
    "description": (
        "An append-only ledger that every sub-agent writes to on a "
        "successful run, with the input prompt, the tool calls, the "
        "final output, and a hash chain so downstream auditors can "
        "verify nothing was tampered with."
    ),
    "market_analysis": (
        "AI auditing teams need per-agent provenance. Today they "
        "scrape logs. A first-class ledger primitive flips the model."
    ),
    "mvp_scope": "Phase 1: append-only ledger. Phase 2: verifier CLI.",
    "tech_stack": ["python", "anthropic", "mcp"],
    "feasibility_score": 0.82,
    "mode_rationale": "Inversion of opaque logging.",
}


def _stub_backend() -> MagicMock:
    b = MagicMock()
    b.name = "stub:opus"
    b.call = MagicMock(return_value=json.dumps(_OK_PAYLOAD))
    return b


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "artifacts.db")
    await d.connect()
    yield d
    await d.close()


# --------------------------------------------------------------------------- #
# Catalog                                                                     #
# --------------------------------------------------------------------------- #


class TestArtifactCatalog:
    def test_all_eight_types_have_prompts(self):
        assert len(ARTIFACT_TYPES) == 8
        for t in ARTIFACT_TYPES:
            assert t in _ARTIFACT_PROMPTS
            assert len(_ARTIFACT_PROMPTS[t]) > 100, f"{t} prompt is too thin"

    def test_known_types_present(self):
        for t in (
            "skill",
            "sub-agent",
            "mcp-server",
            "hook",
            "slash-command",
            "workflow",
            "protocol",
            "ability",
        ):
            assert t in ARTIFACT_TYPES


# --------------------------------------------------------------------------- #
# Picker                                                                      #
# --------------------------------------------------------------------------- #


class TestPickLeastUsedArtifact:
    @pytest.mark.asyncio
    async def test_empty_db_picks_first_type(self, db):
        picked = await pick_least_used_artifact(db, IdeaCategory.AUTOMATION_INCOME)
        assert picked == ARTIFACT_TYPES[0]

    @pytest.mark.asyncio
    async def test_skips_the_saturated_type(self, db):
        for i in range(3):
            idea = Idea(
                name=f"Already skill {i}",
                tagline="t",
                description="d",
                category=IdeaCategory.AUTOMATION_INCOME,
                market_analysis="m",
                feasibility_score=0.7,
                mvp_scope="mvp",
                tech_stack=["python"],
                artifact_type="skill",
            )
            await db.save_idea(idea)
        picked = await pick_least_used_artifact(db, IdeaCategory.AUTOMATION_INCOME)
        assert picked != "skill"


# --------------------------------------------------------------------------- #
# Prompt building                                                             #
# --------------------------------------------------------------------------- #


class TestPromptIncludesArtifact:
    def test_artifact_block_appears_in_prompt(self):
        prompt = _build_prompt(
            category=IdeaCategory.AUTOMATION_INCOME,
            mode="novel",
            persona="someone",
            avoid_list=[],
            artifact_type="mcp-server",
        )
        assert "Artifact type: mcp-server" in prompt
        assert "MCP" in prompt or "tools" in prompt

    def test_no_artifact_block_when_none(self):
        prompt = _build_prompt(
            category=IdeaCategory.SECURITY_TOOL,
            mode="novel",
            persona="someone",
            avoid_list=[],
            artifact_type=None,
        )
        assert "Artifact type:" not in prompt
        # Default headline kept.
        assert "project idea" in prompt


# --------------------------------------------------------------------------- #
# Top-level wiring                                                            #
# --------------------------------------------------------------------------- #


class TestGenerateIdeaLLMArtifact:
    @pytest.mark.asyncio
    async def test_explicit_artifact_overrides_picker(self, db):
        result = await generate_idea_llm(
            db,
            IdeaCategory.AUTOMATION_INCOME,
            mode="adversarial",
            artifact_type="protocol",
            backend=_stub_backend(),
        )
        assert result is not None
        assert result.artifact_type == "protocol"
        assert result.idea.artifact_type == "protocol"

    @pytest.mark.asyncio
    async def test_unknown_artifact_becomes_none(self, db):
        """No category auto-rotates; a junk explicit type falls back to the
        default project-pitch shape, not a picker pick."""
        result = await generate_idea_llm(
            db,
            IdeaCategory.AUTOMATION_INCOME,
            mode="bundle",
            artifact_type="not-a-real-type",
            backend=_stub_backend(),
        )
        assert result is not None
        assert result.artifact_type is None
        assert result.idea.artifact_type is None

    @pytest.mark.asyncio
    async def test_no_auto_rotation_for_remaining_categories(self, db):
        """The artifact-rotation set is empty — no category gets an
        auto-picked artifact shape."""
        result = await generate_idea_llm(
            db,
            IdeaCategory.AUTOMATION_INCOME,
            mode="novel",
            backend=_stub_backend(),
        )
        assert result is not None
        assert result.artifact_type is None
        assert result.idea.artifact_type is None
