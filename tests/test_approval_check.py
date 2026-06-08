"""Tests for the approval-time think-tank sanity checker.

When a human flips an idea to 'approved', a quick heuristic check runs
to catch obvious flow / coherence problems before the idea heads toward
scaffolding. Non-blocking: the approval still completes; the check
result is persisted for dashboard surfacing.

Checks today (heuristic — no LLM call required):
- scope_alignment: description and mvp_scope reference overlapping concepts
- tech_stack_present: tech_stack non-empty
- score_realistic: feasibility_score in [0.4, 0.99]
- super_components_coherent (supers only): ≥3 components, theme cohesion
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.engine.approval_check import validate_idea
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(**overrides) -> Idea:
    base = dict(
        name="Distributed Tracing Anomaly Detector",
        tagline="anomaly detection over distributed tracing data",
        description=(
            "Detects anomalies in distributed-tracing spans by clustering "
            "latency and error patterns across services. Provides per-route "
            "alerts and historical drift analysis."
        ),
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="Real SRE need for tracing anomaly tooling.",
        feasibility_score=0.85,
        mvp_scope=(
            "Phase 1: ingest distributed traces from OTLP. "
            "Phase 2: cluster anomalies. Phase 3: alert per route."
        ),
        tech_stack=["python", "fastapi", "opentelemetry"],
    )
    base.update(overrides)
    return Idea(**base)


def _super_idea(name: str, components: list[str], **overrides) -> Idea:
    body = (
        f"{name} brings together {len(components)} complementary project "
        "concepts into a single, cohesive platform:\n\n"
    )
    body += "\n".join(f"- **{c}**: blurb describing {c}" for c in components)
    return _idea(
        name=f"[SUPER] {name}",
        tagline=f"super tagline for {name}",
        description=body,
        **overrides,
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "approval.db")
    await database.connect()
    yield database
    await database.close()


class TestValidateIdeaRegular:
    def test_passes_well_formed_idea(self):
        result = validate_idea(_idea())
        assert result.verdict == "pass"
        assert all(c["status"] in ("pass", "warn") for c in result.checks)

    def test_fails_when_tech_stack_empty(self):
        result = validate_idea(_idea(tech_stack=[]))
        assert result.verdict == "fail"
        failed = [c for c in result.checks if c["status"] == "fail"]
        assert any(c["name"] == "tech_stack_present" for c in failed)

    def test_warns_on_low_score(self):
        result = validate_idea(_idea(feasibility_score=0.35))
        assert result.verdict in ("warn", "fail")
        score_check = next(c for c in result.checks if c["name"] == "score_realistic")
        assert score_check["status"] != "pass"

    def test_warns_on_perfect_score(self):
        result = validate_idea(_idea(feasibility_score=1.0))
        score_check = next(c for c in result.checks if c["name"] == "score_realistic")
        assert score_check["status"] != "pass"

    def test_fails_on_scope_misalignment(self):
        """Description talks about tracing, MVP scope talks about billing —
        clear flow disconnect."""
        result = validate_idea(_idea(
            mvp_scope=(
                "Phase 1: build a billing dashboard with invoice generation. "
                "Phase 2: integrate Stripe payments and tax calculation."
            ),
        ))
        assert result.verdict in ("warn", "fail")
        align = next(c for c in result.checks if c["name"] == "scope_alignment")
        assert align["status"] != "pass"


class TestValidateIdeaSuper:
    def test_passes_coherent_super(self):
        result = validate_idea(_super_idea(
            "Tracing Health Aggregation",
            [
                "Distributed Tracing Span Sampler",
                "Tracing Anomaly Detector",
                "Tracing Latency Aggregator",
                "Tracing Drift Monitor",
            ],
        ))
        assert result.verdict in ("pass", "warn")

    def test_fails_when_super_has_too_few_components(self):
        result = validate_idea(_super_idea(
            "Lonely Super",
            ["Only Component"],
        ))
        coh = next(c for c in result.checks if c["name"] == "super_components_coherent")
        assert coh["status"] == "fail"

    def test_warns_on_super_with_unrelated_components(self):
        """No theme cohesion: billing + tracing + crypto + emoji."""
        result = validate_idea(_super_idea(
            "Random Bundle",
            [
                "Billing Dashboard",
                "Tracing Anomaly Detector",
                "Crypto Key Rotator",
                "Emoji Picker",
            ],
        ))
        coh = next(c for c in result.checks if c["name"] == "super_components_coherent")
        assert coh["status"] != "pass"


class TestPersistApprovalCheck:
    @pytest.mark.asyncio
    async def test_saves_and_retrieves(self, db):
        from project_forge.engine.approval_check import (
            get_approval_check,
            save_approval_check,
            validate_idea,
        )

        idea = _idea()
        await db.save_idea(idea)
        result = validate_idea(idea)
        await save_approval_check(db, idea.id, result)

        loaded = await get_approval_check(db, idea.id)
        assert loaded is not None
        assert loaded["verdict"] == result.verdict
        assert len(loaded["checks"]) == len(result.checks)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_check(self, db):
        from project_forge.engine.approval_check import get_approval_check

        loaded = await get_approval_check(db, "no-such-id")
        assert loaded is None
