"""Tests for the GH-issue → DB sync cadence.

The UI showed "✓ promoted" + a live "issue ↗" link for ideas whose
GitHub issue had been closed manually by the operator. The DB had no
way to know. This sync periodically pulls live GH state for every
auto-promoted idea and updates the DB:
  - OPEN          → no change
  - CLOSED + COMPLETED   → status='contributed' (operator shipped it)
  - CLOSED + NOT_PLANNED → status='archived' + archived_reason
                            (operator rejected the promotion)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database


def _promoted_idea(
    name: str,
    issue_url: str,
    status: str = "approved",
) -> Idea:
    from datetime import UTC, datetime, timedelta
    idea = Idea(
        name=name,
        tagline="t",
        description="d" * 80,
        category=IdeaCategory.AUTOMATION_INCOME,
        market_analysis="m" * 40,
        feasibility_score=0.8,
        mvp_scope="mvp" * 5,
        tech_stack=["python"],
        status=status,
    )
    idea.auto_promoted_at = datetime.now(UTC) - timedelta(hours=1)
    idea.github_issue_url = issue_url
    return idea


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "issue_sync.db")
    await d.connect()
    yield d
    await d.close()


# --------------------------------------------------------------------------- #
# URL parsing                                                                 #
# --------------------------------------------------------------------------- #


class TestParseIssueRef:
    def test_basic_https_url(self):
        from project_forge.cron.issue_sync_runner import parse_issue_ref

        ref = parse_issue_ref("https://github.com/owner/repo/issues/42")
        assert ref == ("owner/repo", 42)

    def test_url_with_trailing_slash(self):
        from project_forge.cron.issue_sync_runner import parse_issue_ref

        ref = parse_issue_ref("https://github.com/o/r/issues/7/")
        assert ref == ("o/r", 7)

    def test_returns_none_on_garbage(self):
        from project_forge.cron.issue_sync_runner import parse_issue_ref

        assert parse_issue_ref("not a url") is None
        assert parse_issue_ref("") is None
        assert parse_issue_ref("https://github.com/foo") is None


# --------------------------------------------------------------------------- #
# Sync cycle                                                                  #
# --------------------------------------------------------------------------- #


class TestSyncCycle:
    @pytest.mark.asyncio
    async def test_no_promoted_returns_zero(self, db):
        from project_forge.cron.issue_sync_runner import run_issue_sync_cycle

        result = await run_issue_sync_cycle(db)
        assert result["checked"] == 0

    @pytest.mark.asyncio
    async def test_open_issue_leaves_idea_untouched(self, db):
        from project_forge.cron import issue_sync_runner

        idea = _promoted_idea("Open", "https://github.com/o/r/issues/10")
        await db.save_idea(idea)

        with patch.object(
            issue_sync_runner,
            "fetch_issue_state",
            return_value={"state": "OPEN", "reason": None},
        ):
            result = await issue_sync_runner.run_issue_sync_cycle(db)

        assert result["checked"] == 1
        assert result["updated"] == 0
        loaded = await db.get_idea(idea.id)
        assert loaded.status == "approved"

    @pytest.mark.asyncio
    async def test_completed_close_marks_contributed(self, db):
        from project_forge.cron import issue_sync_runner

        idea = _promoted_idea("Done", "https://github.com/o/r/issues/11")
        await db.save_idea(idea)

        with patch.object(
            issue_sync_runner,
            "fetch_issue_state",
            return_value={"state": "CLOSED", "reason": "COMPLETED"},
        ):
            await issue_sync_runner.run_issue_sync_cycle(db)

        loaded = await db.get_idea(idea.id)
        assert loaded.status == "contributed"
        # History is preserved.
        assert loaded.auto_promoted_at is not None
        assert loaded.github_issue_url == "https://github.com/o/r/issues/11"

    @pytest.mark.asyncio
    async def test_not_planned_close_archives_idea(self, db):
        from project_forge.cron import issue_sync_runner

        idea = _promoted_idea("Rejected", "https://github.com/o/r/issues/12")
        await db.save_idea(idea)

        with patch.object(
            issue_sync_runner,
            "fetch_issue_state",
            return_value={"state": "CLOSED", "reason": "NOT_PLANNED"},
        ):
            await issue_sync_runner.run_issue_sync_cycle(db)

        loaded = await db.get_idea(idea.id)
        assert loaded.status == "archived"

    @pytest.mark.asyncio
    async def test_already_resolved_skipped(self, db):
        """Don't re-process ideas the sync has already moved past 'approved'."""
        from project_forge.cron import issue_sync_runner

        idea = _promoted_idea(
            "Already done",
            "https://github.com/o/r/issues/13",
            status="contributed",
        )
        await db.save_idea(idea)

        called = []
        def _fetch(_repo, _n):
            called.append((_repo, _n))
            return {"state": "OPEN", "reason": None}

        with patch.object(issue_sync_runner, "fetch_issue_state", _fetch):
            result = await issue_sync_runner.run_issue_sync_cycle(db)

        # Sync only looks at status='approved'; contributed/archived stay put.
        assert called == []
        assert result["checked"] == 0

    @pytest.mark.asyncio
    async def test_gh_failure_does_not_abort_others(self, db):
        from project_forge.cron import issue_sync_runner

        ok = _promoted_idea("OK", "https://github.com/o/r/issues/20")
        bad = _promoted_idea("Bad URL", "not-a-url")
        await db.save_idea(ok)
        await db.save_idea(bad)

        with patch.object(
            issue_sync_runner,
            "fetch_issue_state",
            return_value={"state": "CLOSED", "reason": "COMPLETED"},
        ):
            result = await issue_sync_runner.run_issue_sync_cycle(db)

        # 'ok' got synced, 'bad' was skipped because the URL didn't parse.
        assert result["checked"] >= 1
        loaded_ok = await db.get_idea(ok.id)
        assert loaded_ok.status == "contributed"
        loaded_bad = await db.get_idea(bad.id)
        assert loaded_bad.status == "approved"  # untouched
