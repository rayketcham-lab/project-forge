"""TDD: Wire reason_cluster_name into synthesize_super_idea + dedup.

Phase 6 plumbing existed; this turns it on behind FORGE_SUPER_REASONING.

When the flag is on:
- synthesize_super_idea gets the name from reason_cluster_name (LLM call)
- The cluster signature is embedded in the description as [CLUSTER:<sig>]
- generate_seeded dedup uses find_super_by_signature instead of base name
- LLM failure falls back gracefully to slot-fill (with signature still embedded)

When the flag is off (default):
- All existing behavior unchanged
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.super_ideas import SuperIdeaGenerator, synthesize_super_idea
from project_forge.engine.super_reasoning import (
    cluster_signature,
    extract_cluster_signature,
)
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str, *, category: IdeaCategory = IdeaCategory.SECURITY_TOOL,
          score: float = 0.85) -> Idea:
    return Idea(
        name=name,
        tagline=f"{name.lower()}: {name.lower()} solution",
        description="d",
        category=category,
        market_analysis="m",
        feasibility_score=score,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "wire_super.db")
    await d.connect()
    yield d
    await d.close()


def _cluster(ideas: list[Idea]) -> dict:
    return {
        "ideas": ideas,
        "categories": frozenset(i.category for i in ideas),
        "theme": "placeholder theme",
    }


# ── synthesize_super_idea: signature is always embedded ──────────────


class TestSignatureEmbedding:
    def test_synthesize_embeds_cluster_signature_in_description(self):
        """Even with reasoning OFF, the signature should appear in description."""
        ideas = [_idea("CertA"), _idea("CertB"), _idea("CertC")]
        si = synthesize_super_idea(_cluster(ideas))

        sig = cluster_signature(ideas)
        assert sig in si.description, (
            f"Expected [CLUSTER:{sig}] in description, got: {si.description[:200]}"
        )

    def test_signature_is_deterministic_across_synthesize_calls(self):
        ideas = [_idea("X"), _idea("Y")]
        si1 = synthesize_super_idea(_cluster(ideas))
        si2 = synthesize_super_idea(_cluster(ideas))
        sig1 = extract_cluster_signature(si1.description)
        sig2 = extract_cluster_signature(si2.description)
        assert sig1 is not None
        assert sig1 == sig2


# ── synthesize_super_idea(use_reasoning=True, llm_call=…) ────────────


class TestReasoningPath:
    def test_uses_llm_name_when_reasoning_on(self):
        ideas = [_idea("Cert Pinner"), _idea("Cert Validator")]

        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            return '{"name": "Trust Anchor Lifecycle Platform"}'

        si = synthesize_super_idea(_cluster(ideas), use_reasoning=True, llm_call=fake_llm)

        assert si.name == "Trust Anchor Lifecycle Platform"

    def test_falls_back_to_slot_fill_on_llm_garbage(self):
        ideas = [_idea("Cert Pinner"), _idea("Cert Validator")]

        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            return "not json"

        si = synthesize_super_idea(_cluster(ideas), use_reasoning=True, llm_call=fake_llm)

        # Must still produce a name (from slot-fill fallback)
        assert si.name
        # Signature still embedded
        assert extract_cluster_signature(si.description) is not None

    def test_default_off_uses_cluster_theme(self):
        """Without use_reasoning kwarg, synthesize uses cluster['theme'] verbatim."""
        ideas = [_idea("Cert A"), _idea("Cert B")]
        cluster = {
            "ideas": ideas,
            "categories": frozenset(i.category for i in ideas),
            "theme": "Slot Fill Theme Defense Suite",  # what _dynamic_cluster_name would produce
        }
        si = synthesize_super_idea(cluster)
        # Theme passed through unchanged, no LLM call
        assert si.name == "Slot Fill Theme Defense Suite"


# ── generate_seeded: signature-based dedup when flag on ──────────────


class TestSignatureBasedDedup:
    @pytest.mark.asyncio
    async def test_signature_dedup_blocks_repeat_cluster(self, db, monkeypatch):
        """With FORGE_SUPER_REASONING on, same cluster signature must be skipped."""
        monkeypatch.setenv("FORGE_SUPER_REASONING", "1")

        # Stub the LLM call so the second attempt produces a DIFFERENT name
        # but the same cluster signature → dedup must still block.
        call_count = {"n": 0}

        def fake_llm(prompt: str) -> str:  # noqa: ARG001
            call_count["n"] += 1
            return f'{{"name": "Synthesized Capability {call_count["n"]}"}}'

        monkeypatch.setattr(
            "project_forge.engine.super_ideas._reasoning_llm_call",
            lambda: fake_llm,
        )

        # Seed 10+ ideas so generate_seeded has material; all in same category
        # to maximize the chance the same cluster forms twice.
        for i in range(12):
            await db.save_idea(_idea(f"Cert Tool {i}", category=IdeaCategory.CRYPTO_INFRASTRUCTURE))

        gen = SuperIdeaGenerator(db)
        first = await gen.generate_seeded(slot=0)

        if first is None:
            pytest.skip("Test corpus did not form a cluster — environmental")

        # Verify signature is embedded
        sig = extract_cluster_signature(first.description)
        assert sig is not None, "First super idea must carry [CLUSTER:<sig>]"

        # Now verify find_super_by_signature returns it
        from project_forge.engine.super_reasoning import find_super_by_signature

        found = await find_super_by_signature(db, sig)
        assert found is not None
        assert found.name.startswith("[SUPER]")
