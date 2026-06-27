"""TDD: Direction C — super-idea reasoning over slot-filling.

Phase 6 (issue #59). Replaces the templatic _dynamic_cluster_name slot-fill
with a Claude reasoning call that NAMES the unifying capability of a
cluster. New dedup anchor is the cluster signature (SHA256 of sorted
member-idea IDs), not the name pattern — so dedup keeps working without
relying on name regularity.

Behind feature flag FORGE_SUPER_REASONING. Off by default until shadow
validation greenlights an improvement.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.super_reasoning import (
    cluster_signature,
    reason_cluster_name,
)
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, *, idea_id: str | None = None, category: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> Idea:
    i = Idea(
        name=name,
        tagline=f"tag for {name}",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=0.85,
        mvp_scope="mvp",
        tech_stack=["python"],
    )
    if idea_id is not None:
        i.id = idea_id
    return i


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "super_reasoning.db")
    await d.connect()
    yield d
    await d.close()


# ── cluster_signature ────────────────────────────────────────────────


class TestClusterSignature:
    def test_deterministic_on_same_inputs(self):
        ideas = [_idea("A", idea_id="aaa"), _idea("B", idea_id="bbb")]
        s1 = cluster_signature(ideas)
        s2 = cluster_signature(ideas)
        assert s1 == s2

    def test_order_independent(self):
        a = _idea("A", idea_id="aaa")
        b = _idea("B", idea_id="bbb")
        assert cluster_signature([a, b]) == cluster_signature([b, a])

    def test_differs_for_different_member_set(self):
        a = _idea("A", idea_id="aaa")
        b = _idea("B", idea_id="bbb")
        c = _idea("C", idea_id="ccc")
        assert cluster_signature([a, b]) != cluster_signature([a, c])

    def test_returns_string(self):
        sig = cluster_signature([_idea("A", idea_id="x")])
        assert isinstance(sig, str)
        assert len(sig) >= 12  # at least 12 hex chars

    def test_empty_cluster_returns_empty_signature(self):
        # Edge case: empty member list
        sig = cluster_signature([])
        assert sig == ""


# ── reason_cluster_name ──────────────────────────────────────────────


class TestReasonClusterName:
    def test_calls_llm_with_member_ideas(self):
        ideas = [_idea("Cert Pinner"), _idea("Cert Validator")]
        captured = {}

        def fake_llm(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"name": "Trust Anchor Lifecycle Platform"}'

        name = reason_cluster_name(ideas, llm_call=fake_llm)

        assert name == "Trust Anchor Lifecycle Platform"
        assert "Cert Pinner" in captured["prompt"]
        assert "Cert Validator" in captured["prompt"]

    def test_strips_super_prefix_if_llm_added_it(self):
        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            return '{"name": "[SUPER] Trust Anchor Platform"}'

        name = reason_cluster_name([_idea("X")], llm_call=fake_llm)
        # Caller should not include [SUPER] — it's added at storage time
        assert not name.startswith("[SUPER]")

    def test_falls_back_when_llm_returns_garbage(self):
        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            return "not json"

        # Must not crash; returns None or a fallback
        name = reason_cluster_name([_idea("Cert A"), _idea("Cert B")], llm_call=fake_llm)
        assert name is None or isinstance(name, str)

    def test_falls_back_when_name_missing_in_response(self):
        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            return '{"description": "x"}'

        name = reason_cluster_name([_idea("X")], llm_call=fake_llm)
        assert name is None

    def test_prompt_includes_constraint_to_name_capability_gap(self):
        captured = {}

        def fake_llm(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"name": "X"}'

        reason_cluster_name([_idea("A"), _idea("B")], llm_call=fake_llm)

        # Prompt should ask Claude to name the unifying capability/gap
        assert "capability" in captured["prompt"].lower() or "gap" in captured["prompt"].lower()


# ── DB lookup by cluster_signature ───────────────────────────────────


class TestFindSuperByClusterSignature:
    @pytest.mark.asyncio
    async def test_finds_existing_super_with_matching_signature(self, db):
        from project_forge.engine.super_reasoning import find_super_by_signature

        # Seed a super idea with a known cluster_signature in its description.
        # Until the schema gains a dedicated column, the contract is: signature
        # is stored as a tag in the description: "[CLUSTER:<sig>]"
        sig = "abc123def456"
        idea = _idea("[SUPER] Test Super")
        idea.description = f"Some description.\n\n[CLUSTER:{sig}]"
        await db.save_idea(idea)

        found = await find_super_by_signature(db, sig)
        assert found is not None
        assert found.id == idea.id

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self, db):
        from project_forge.engine.super_reasoning import find_super_by_signature

        result = await find_super_by_signature(db, "no-such-sig")
        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_archived_supers(self, db):
        from project_forge.engine.super_reasoning import find_super_by_signature

        sig = "abc123"
        idea = _idea("[SUPER] Old")
        idea.description = f"d\n\n[CLUSTER:{sig}]"
        await db.save_idea(idea)
        await db.db.execute("UPDATE ideas SET status='archived' WHERE id=?", (idea.id,))
        await db.db.commit()

        result = await find_super_by_signature(db, sig)
        assert result is None
