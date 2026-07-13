"""Tests for the Think Tank reconciler (#91).

The reconciler autonomously marks shipped self-improvement suggestions
as implemented by matching them against recent commit subjects. It must
be conservative: no match → untouched; it only ever promotes to
implemented, never archives or rejects.

Commit subjects are the only trusted signal — closed promoted issues
were tried and refuted the same day (five '[Think Tank] Decompose X'
issues were closed COMPLETED while the target files had doubled in
size), so there is deliberately no issue-based matching here.
"""

import pytest

from project_forge.engine.thinktank_reconcile import reconcile_thinktank
from project_forge.models import Idea, IdeaCategory


def make_si(name: str, status: str = "new", category: IdeaCategory = IdeaCategory.SELF_IMPROVEMENT) -> Idea:
    return Idea(
        name=name,
        tagline="a concrete improvement to the forge codebase",
        description="A specific defect in src/project_forge/ that needs a targeted fix with tests.",
        category=category,
        market_analysis="Improves forge reliability.",
        feasibility_score=0.8,
        mvp_scope="Patch the module and add regression tests.",
        status=status,
    )


class TestReconcileByCommit:
    @pytest.mark.asyncio
    async def test_commit_containing_all_name_tokens_matches(self, db):
        idea = make_si("Churn Endpoint Rate Limit")
        await db.save_idea(idea)

        report = await reconcile_thinktank(db, commit_subjects=["fix(web): rate limit the churn endpoint"])

        stored = await db.get_idea(idea.id)
        assert stored.status == "implemented"
        # Provenance lands in the archived_reason column (raw-SQL only, #71)
        cur = await db.db.execute("SELECT archived_reason FROM ideas WHERE id=?", (idea.id,))
        (reason,) = await cur.fetchone()
        assert reason is not None
        assert "rate limit the churn endpoint" in reason
        assert idea.id in report["implemented"]

    @pytest.mark.asyncio
    async def test_approved_ideas_also_reconciled(self, db):
        idea = make_si("Churn Endpoint Rate Limit", status="approved")
        await db.save_idea(idea)

        await reconcile_thinktank(db, commit_subjects=["fix(web): rate limit the churn endpoint"])

        stored = await db.get_idea(idea.id)
        assert stored.status == "implemented"

    @pytest.mark.asyncio
    async def test_weak_overlap_does_not_match(self, db):
        """One shared word with a commit must never mark an idea shipped."""
        idea = make_si("Mission Idea Cap")
        await db.save_idea(idea)

        await reconcile_thinktank(
            db,
            commit_subjects=["feat(missions): operator-directed generation — point the think tank at a target"],
        )

        stored = await db.get_idea(idea.id)
        assert stored.status == "new"

    @pytest.mark.asyncio
    async def test_single_significant_token_never_matches(self, db):
        """Names that reduce to <2 significant tokens are too ambiguous to auto-match."""
        idea = make_si("Fix The Scheduler")  # 'fix'/'the' are stop tokens → only {scheduler}
        await db.save_idea(idea)

        await reconcile_thinktank(db, commit_subjects=["fix(web): scheduler tweak"])

        stored = await db.get_idea(idea.id)
        assert stored.status == "new"

    @pytest.mark.asyncio
    async def test_no_signals_touches_nothing(self, db):
        idea = make_si("Sanitize Request ID Header")
        await db.save_idea(idea)

        report = await reconcile_thinktank(db, commit_subjects=[])

        stored = await db.get_idea(idea.id)
        assert stored.status == "new"
        assert report["implemented"] == []


class TestReconcileScope:
    @pytest.mark.asyncio
    async def test_inactive_ideas_untouched(self, db):
        idea = make_si("Churn Endpoint Rate Limit", status="rejected")
        await db.save_idea(idea)

        await reconcile_thinktank(db, commit_subjects=["fix(web): rate limit the churn endpoint"])

        stored = await db.get_idea(idea.id)
        assert stored.status == "rejected"

    @pytest.mark.asyncio
    async def test_non_si_categories_untouched(self, db):
        idea = make_si("Churn Endpoint Rate Limit", category=IdeaCategory.SECURITY_TOOL)
        await db.save_idea(idea)

        await reconcile_thinktank(db, commit_subjects=["fix(web): rate limit the churn endpoint"])

        stored = await db.get_idea(idea.id)
        assert stored.status == "new"

    @pytest.mark.asyncio
    async def test_report_counts_scanned_ideas(self, db):
        await db.save_idea(make_si("Churn Endpoint Rate Limit"))
        await db.save_idea(make_si("Atomic Nudge Reload"))

        report = await reconcile_thinktank(db, commit_subjects=["fix(web): rate limit the churn endpoint"])

        assert report["scanned"] == 2
        assert len(report["implemented"]) == 1


class TestCadenceWiring:
    """The introspect cadence self-cleans before generating (#91) — hermetic, no real git."""

    @pytest.mark.asyncio
    async def test_fire_introspect_runs_reconciler_with_gathered_subjects(self, db, monkeypatch):
        from unittest.mock import AsyncMock

        from project_forge.web import lifespan_scheduler as ls

        calls: dict = {}

        async def fake_reconcile(db_, subjects):
            calls["subjects"] = subjects
            return {"scanned": 0, "implemented": []}

        monkeypatch.setattr("project_forge.engine.thinktank_reconcile.reconcile_thinktank", fake_reconcile)
        monkeypatch.setattr("project_forge.cron.introspect_runner._recent_commit_subjects", lambda: ["c1"])
        monkeypatch.setattr(
            "project_forge.cron.introspect_runner.run_introspect_cycle",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(ls, "_resolve_generator", lambda: None)

        await ls._fire_introspect(db)

        assert calls["subjects"] == ["c1"]
