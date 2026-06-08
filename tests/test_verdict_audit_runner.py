"""Tests for the verdict meta-audit ('who watches the watcher') cadence.

Premise: challenge / review verdicts come from an LLM. If the LLM is
confidently wrong, the engine is confidently wrong. This runner samples
recent verdicts, re-runs them with a different tone (skeptical ↔
curious), compares, and persists divergences for review.

The sampling is deliberately small (10% default) so we don't double the
LLM bill. Divergence is a number in [0.0, 1.0] derived from the verdict
space — same verdict = 0.0, opposite (kill ↔ strengthen) = 1.0.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from project_forge.models import Challenge, Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str = "x") -> Idea:
    return Idea(
        name=name,
        tagline="t",
        description="d",
        category=IdeaCategory.OBSERVABILITY,
        market_analysis="m",
        feasibility_score=0.8,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "audit.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------- #
# Divergence math                                                             #
# --------------------------------------------------------------------------- #


class TestDivergence:
    def test_identical_verdicts_zero(self):
        from project_forge.cron.verdict_audit_runner import verdict_divergence

        assert verdict_divergence("strengthen", "strengthen") == 0.0

    def test_opposite_verdicts_one(self):
        from project_forge.cron.verdict_audit_runner import verdict_divergence

        # 'kill' vs 'strengthen' are at opposite ends.
        assert verdict_divergence("kill", "strengthen") == 1.0

    def test_partial_divergence_in_between(self):
        from project_forge.cron.verdict_audit_runner import verdict_divergence

        d = verdict_divergence("no_change", "strengthen")
        assert 0.0 < d < 1.0


# --------------------------------------------------------------------------- #
# Run a full cycle                                                            #
# --------------------------------------------------------------------------- #


class TestRunAuditCycle:
    @pytest.mark.asyncio
    async def test_no_verdicts_returns_zero(self, db):
        from project_forge.cron.verdict_audit_runner import run_verdict_audit_cycle

        result = await run_verdict_audit_cycle(db, sample_rate=1.0)
        assert result == {"audited": 0, "divergences": 0, "results": []}

    @pytest.mark.asyncio
    async def test_audits_a_sampled_challenge(self, db):
        """The runner picks a sampled challenge, re-runs it, persists the audit."""
        from project_forge.cron import verdict_audit_runner

        idea = _idea("Sampled")
        await db.save_idea(idea)
        challenge = Challenge(
            idea_id=idea.id,
            question="why",
            response="because",
            verdict="strengthen",
            confidence=0.8,
        )
        await db.save_challenge(challenge)

        # Stub the re-challenge to return a divergent verdict.
        async def _stub_re(idea_obj, question, original_verdict, original_tone):
            return {
                "response": "second opinion",
                "verdict": "narrow",
                "confidence": 0.6,
                "audit_notes": "re-evaluated with curious tone",
            }

        from unittest.mock import patch
        with patch.object(verdict_audit_runner, "_re_evaluate_challenge", _stub_re):
            result = await verdict_audit_runner.run_verdict_audit_cycle(
                db, sample_rate=1.0,
            )
        assert result["audited"] == 1
        # 'strengthen' → 'narrow' is a non-trivial divergence.
        assert result["divergences"] >= 1
        # Persisted to verdict_audits.
        cur = await db.db.execute("SELECT COUNT(*) FROM verdict_audits")
        assert (await cur.fetchone())[0] == 1

    @pytest.mark.asyncio
    async def test_sample_rate_caps_audits(self, db):
        """sample_rate=0.5 over 10 challenges audits roughly 5 (deterministic
        seed makes it exactly 5)."""
        from project_forge.cron import verdict_audit_runner

        idea = _idea("Bulk")
        await db.save_idea(idea)
        for i in range(10):
            await db.save_challenge(Challenge(
                idea_id=idea.id,
                question=f"q{i}",
                response="r",
                verdict="strengthen",
                confidence=0.5,
            ))

        async def _stub_re(idea_obj, q, ov, ot):
            return {"response": "ok", "verdict": "strengthen", "confidence": 0.7,
                    "audit_notes": ""}

        from unittest.mock import patch
        with patch.object(verdict_audit_runner, "_re_evaluate_challenge", _stub_re):
            result = await verdict_audit_runner.run_verdict_audit_cycle(
                db, sample_rate=0.5, seed=42,
            )
        assert result["audited"] == 5

    @pytest.mark.asyncio
    async def test_skips_already_audited_challenges(self, db):
        from project_forge.cron import verdict_audit_runner

        idea = _idea()
        await db.save_idea(idea)
        challenge = Challenge(
            idea_id=idea.id,
            question="q",
            response="r",
            verdict="strengthen",
            confidence=0.7,
        )
        await db.save_challenge(challenge)

        async def _stub(idea_obj, q, ov, ot):
            return {"response": "x", "verdict": "no_change", "confidence": 0.5,
                    "audit_notes": ""}

        from unittest.mock import patch
        with patch.object(verdict_audit_runner, "_re_evaluate_challenge", _stub):
            first = await verdict_audit_runner.run_verdict_audit_cycle(
                db, sample_rate=1.0,
            )
            second = await verdict_audit_runner.run_verdict_audit_cycle(
                db, sample_rate=1.0,
            )
        assert first["audited"] == 1
        assert second["audited"] == 0
