"""Forge Mechanic engine (#100) — selection + isolated orchestration.

The mechanic picks the top Think Tank item, implements it with a scoped
`claude -p` agent in a throwaway worktree, gates on the full suite + ruff,
and opens a PR (never merges). These tests cover the selection ranking and
the orchestration state machine with every subprocess seam mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

# Sentinel worktree path — never touched (every subprocess seam is patched);
# kept off /tmp so ruff's S108 stays quiet.
FAKE_WT = Path("mechanic-wt-mock")


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "mechanic.db")
    await database.connect()
    yield database
    await database.close()


def _si(name: str, **over) -> Idea:
    base = dict(
        name=name,
        tagline=f"{name.lower()} for the engine",
        description="A concrete, scoped self-improvement to the codebase.",
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis="Improves engine reliability.",
        feasibility_score=0.6,
        mvp_scope="One module change plus a test.",
        tech_stack=["python"],
        content_hash=f"mech-{name.lower().replace(' ', '-')[:36]}",
    )
    base.update(over)
    return Idea(**base)


# --------------------------------------------------------------------------- #
# selection / ranking                                                         #
# --------------------------------------------------------------------------- #


class TestPriority:
    def test_security_item_outranks_plain(self):
        from project_forge.engine.mechanic import priority_score

        plain = _si("Refactor helper", feasibility_score=0.6)
        secure = _si("Close the SSRF DNS rebind gap", feasibility_score=0.6)
        assert priority_score(secure) > priority_score(plain)

    def test_approved_outranks_new_same_content(self):
        from project_forge.engine.mechanic import priority_score

        new = _si("Bound dedup scans", feasibility_score=0.6)
        approved = _si("Bound dedup scans", feasibility_score=0.6)
        approved.status = "approved"
        assert priority_score(approved) > priority_score(new)

    def test_base_is_feasibility(self):
        from project_forge.engine.mechanic import priority_score

        assert priority_score(_si("Plain tweak", feasibility_score=0.42)) == pytest.approx(0.42)


class TestRankAndSelect:
    @pytest.mark.asyncio
    async def test_rank_only_active_self_improvement(self, db):
        from project_forge.engine.mechanic import rank_work

        await db.save_idea(_si("Active SI"))
        archived = _si("Archived SI", content_hash="mech-arch")
        archived.status = "archived"
        await db.save_idea(archived)
        # A non-SI active idea must not appear.
        await db.save_idea(_si("Money thing", category=IdeaCategory.MICRO_SAAS, content_hash="mech-money"))

        ranked = await rank_work(db)
        names = [i.name for i in ranked]
        assert names == ["Active SI"]

    @pytest.mark.asyncio
    async def test_rank_orders_security_first(self, db):
        from project_forge.engine.mechanic import rank_work

        await db.save_idea(_si("Plain refactor", feasibility_score=0.7, content_hash="mech-a"))
        await db.save_idea(_si("Redact git token leak", feasibility_score=0.6, content_hash="mech-b"))
        ranked = await rank_work(db)
        assert ranked[0].name == "Redact git token leak"

    @pytest.mark.asyncio
    async def test_select_skips_excluded_and_none_when_empty(self, db):
        from project_forge.engine.mechanic import select_work

        assert await select_work(db) is None
        a = _si("Only item", content_hash="mech-only")
        await db.save_idea(a)
        assert (await select_work(db)).id == a.id
        assert await select_work(db, exclude_ids={a.id}) is None


# --------------------------------------------------------------------------- #
# orchestration state machine                                                 #
# --------------------------------------------------------------------------- #


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestOrchestration:
    @pytest.mark.asyncio
    async def test_no_work_when_queue_empty(self, db):
        from project_forge.engine.mechanic import run_mechanic_cycle

        result = await run_mechanic_cycle(db)
        assert result.status == "no_work"

    @pytest.mark.asyncio
    async def test_happy_path_opens_pr_and_cleans_up(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Do the thing", content_hash="mech-happy"))
        removed = {"n": 0}

        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", lambda wt, prompt, **kw: _Proc(0)),
            patch.object(m, "_changed_paths", lambda wt: ["src/project_forge/engine/fundability.py"]),
            patch.object(m, "_quality_gate", lambda wt: (True, "ok")),
            patch.object(m, "_open_pr", lambda wt, branch, idea: "https://github.com/x/y/pull/9"),
            patch.object(m, "_remove_workspace", lambda ws: removed.__setitem__("n", removed["n"] + 1)),
        ):
            result = await m.run_mechanic_cycle(db)

        assert result.status == "pr_opened"
        assert result.pr_url.endswith("/pull/9")
        assert removed["n"] == 1  # worktree always cleaned up

    @pytest.mark.asyncio
    async def test_gate_failure_opens_no_pr(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Break the tests", content_hash="mech-gate"))
        pr_called = {"n": 0}

        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", lambda wt, prompt, **kw: _Proc(0)),
            patch.object(m, "_changed_paths", lambda wt: ["src/project_forge/engine/x.py"]),
            patch.object(m, "_quality_gate", lambda wt: (False, "pytest failed: 3 errors")),
            patch.object(m, "_open_pr", lambda wt, branch, idea: pr_called.__setitem__("n", 1) or "url"),
            patch.object(m, "_remove_workspace", lambda ws: None),
        ):
            result = await m.run_mechanic_cycle(db)

        assert result.status == "gate_failed"
        assert pr_called["n"] == 0  # never opens a PR on a red gate

    @pytest.mark.asyncio
    async def test_agent_failure_short_circuits(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Agent dies", content_hash="mech-agentfail"))
        gate_called = {"n": 0}

        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", lambda wt, prompt, **kw: _Proc(1, stderr="boom")),
            patch.object(m, "_quality_gate", lambda wt: gate_called.__setitem__("n", 1) or (True, "ok")),
            patch.object(m, "_remove_workspace", lambda ws: None),
        ):
            result = await m.run_mechanic_cycle(db)

        assert result.status == "agent_failed"
        assert gate_called["n"] == 0

    @pytest.mark.asyncio
    async def test_no_change_when_agent_edits_nothing(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Noop", content_hash="mech-noop"))
        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", lambda wt, prompt, **kw: _Proc(0)),
            patch.object(m, "_changed_paths", lambda wt: []),
            patch.object(m, "_remove_workspace", lambda ws: None),
        ):
            result = await m.run_mechanic_cycle(db)
        assert result.status == "no_change"

    @pytest.mark.asyncio
    async def test_forbidden_path_blocks_pr(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Sneaky", content_hash="mech-sneaky"))
        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", lambda wt, prompt, **kw: _Proc(0)),
            # Agent tried to edit its own guardrail file.
            patch.object(m, "_changed_paths", lambda wt: ["src/project_forge/engine/mechanic.py"]),
            patch.object(m, "_quality_gate", lambda wt: (True, "ok")),
            patch.object(m, "_remove_workspace", lambda ws: None),
        ):
            result = await m.run_mechanic_cycle(db)
        assert result.status == "gate_failed"
        assert "forbidden" in result.detail

    @pytest.mark.asyncio
    async def test_worktree_removed_even_on_exception(self, db):
        import project_forge.engine.mechanic as m

        await db.save_idea(_si("Blow up mid-run", content_hash="mech-boom"))
        removed = {"n": 0}

        def _boom(wt, prompt, **kw):
            raise RuntimeError("subprocess exploded")

        with (
            patch.object(m, "_create_workspace", lambda branch: FAKE_WT),
            patch.object(m, "run_agent", _boom),
            patch.object(m, "_remove_workspace", lambda ws: removed.__setitem__("n", removed["n"] + 1)),
        ):
            with pytest.raises(RuntimeError):
                await m.run_mechanic_cycle(db)

        assert removed["n"] == 1  # finally: cleanup ran despite the exception


class TestGuardrails:
    def test_forbidden_is_the_leash_not_app_files(self):
        from project_forge.engine.mechanic import _forbidden_touched

        # The mechanic's own leash is off-limits (self-modification escape).
        assert _forbidden_touched(["src/project_forge/engine/mechanic.py"]) is not None
        assert _forbidden_touched(["src/project_forge/engine/mechanic_review.py"]) is not None
        assert _forbidden_touched(["src/project_forge/cron/self_improve_runner.py"]) is not None
        assert _forbidden_touched([".github/workflows/ci.yml"]) is not None
        assert _forbidden_touched([".claude/settings.json"]) is not None
        # But sensitive app files ARE editable — the PR review panel gates
        # them (they're the security backlog's actual targets).
        assert _forbidden_touched(["src/project_forge/web/app.py"]) is None
        assert _forbidden_touched(["src/project_forge/storage/db.py"]) is None
        assert _forbidden_touched(["src/project_forge/engine/fundability.py"]) is None

    def test_allowed_tools_never_bypass_permissions(self):
        from project_forge.engine.mechanic import AGENT_ALLOWED_TOOLS

        joined = " ".join(AGENT_ALLOWED_TOOLS).lower()
        assert "dangerously" not in joined
        assert "bash(git" not in joined  # orchestrator owns commits, not the agent

    def test_run_agent_sends_prompt_via_stdin_not_argv(self, monkeypatch):
        """Regression (found by live validation): --allowedTools is variadic
        and would swallow a positional prompt, so the prompt MUST go via
        stdin."""
        import project_forge.engine.mechanic as m

        captured = {}

        def _fake_run(argv, **kw):
            captured["argv"] = argv
            captured["input"] = kw.get("input")
            return _Proc(0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        monkeypatch.setattr("project_forge.engine.llm_backend._claude_cli_path", lambda: "claude")
        m.run_agent(FAKE_WT, "MY UNIQUE PROMPT TEXT")

        assert captured["input"] == "MY UNIQUE PROMPT TEXT"
        assert "MY UNIQUE PROMPT TEXT" not in captured["argv"]
        assert "--allowedTools" in captured["argv"]
