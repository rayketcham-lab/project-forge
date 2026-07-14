"""Tests for the introspection cron runner and schedule messaging."""

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from project_forge.models import Idea, IdeaCategory
from project_forge.web.app import app, db


@pytest_asyncio.fixture
async def client(tmp_path):
    db.db_path = tmp_path / "test_introspect.db"
    await db.connect()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await db.close()


# --- Cron runner tests ---


class TestIntrospectRunner:
    """Tests for the introspection cron entry point."""

    @pytest.mark.asyncio
    async def test_run_introspect_cycle_generates_idea(self):
        """run_introspect_cycle should generate and store a self-improvement idea."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        mock_generator = MagicMock()
        fake_idea = Idea(
            name="Add structured logging",
            tagline="Better observability",
            description=(
                "The engine module in src/project_forge/engine/ lacks structured logging. "
                "Add structlog with correlation IDs for better observability and debugging."
            ),
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Improves debugging of the forge engine. Target metric: unexplained errors per week.",
            feasibility_score=0.85,
            mvp_scope="Add structlog to src/project_forge/engine/ and tests/test_logging.py.",
            tech_stack=["python", "structlog"],
        )
        mock_generator.generate = AsyncMock(return_value=fake_idea)

        async def _mock_filter_and_save(idea, db):
            await db.save_idea(idea)
            return idea, True, None

        with (
            patch(
                "project_forge.cron.introspect_runner.gather_self_context",
                return_value={
                    "open_issues": [],
                    "recent_commits": [],
                    "test_count": 10,
                    "lint_status": "clean",
                    "code_stats": {"src": 1000, "tests": 500},
                },
            ),
            patch(
                "project_forge.cron.introspect_runner.filter_and_save",
                side_effect=_mock_filter_and_save,
            ),
        ):
            idea = await run_introspect_cycle(mock_db, mock_generator)

        assert idea is not None
        assert idea.category == IdeaCategory.SELF_IMPROVEMENT
        mock_db.save_idea.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_introspect_cycle_avoids_recent_names(self):
        """run_introspect_cycle passes recent self-improvement names to prompt builder."""
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        existing = Idea(
            name="Old improvement",
            tagline="Already suggested",
            description="Already suggested.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Internal.",
            feasibility_score=0.7,
            mvp_scope="Done.",
            tech_stack=["python"],
        )
        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[existing])
        mock_db.save_idea = AsyncMock()

        mock_generator = MagicMock()
        fake_idea = Idea(
            name="New improvement",
            tagline="Fresh idea",
            description="Something new.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Internal.",
            feasibility_score=0.8,
            mvp_scope="Build it.",
            tech_stack=["python"],
        )
        mock_generator.generate = AsyncMock(return_value=fake_idea)

        async def _mock_filter_and_save(idea, db):
            await db.save_idea(idea)
            return idea, True, None

        with (
            patch(
                "project_forge.cron.introspect_runner.gather_self_context",
                return_value={
                    "open_issues": [],
                    "recent_commits": [],
                    "test_count": 10,
                    "lint_status": "clean",
                    "code_stats": {},
                },
            ),
            patch("project_forge.cron.introspect_runner.build_introspection_prompt") as mock_prompt,
            patch(
                "project_forge.cron.introspect_runner.filter_and_save",
                side_effect=_mock_filter_and_save,
            ),
        ):
            mock_prompt.return_value = "fake prompt"
            await run_introspect_cycle(mock_db, mock_generator)

        # Should have passed "Old improvement" as a recent name to avoid
        mock_prompt.assert_called_once()
        recent_names = mock_prompt.call_args[0][1]
        assert "Old improvement" in recent_names


class TestIntrospectModeRotation:
    """The introspect cycle alternates code-fix and generation modes (#90).

    The telemetry-grounded generation-mode prompt existed but was dead code —
    no caller ever passed mode="generation", so saturation/filter-rate/novelty
    signals never reached the LLM.
    """

    def _si_idea(self, name: str, generation_mode: str | None = None) -> Idea:
        return Idea(
            name=name,
            tagline="a concrete improvement",
            description="A specific fix in src/project_forge/engine/ with tests to match.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Target metric: filter rate. Improves generation quality.",
            feasibility_score=0.8,
            mvp_scope="Patch src/project_forge/engine/llm_generator.py and add tests.",
            tech_stack=["python"],
            generation_mode=generation_mode,
        )

    def _patches(self, mode_calls: dict):
        """Common patch set capturing prompt kwargs and neutralizing I/O."""

        async def _mock_filter_and_save(idea, db):
            await db.save_idea(idea)
            return idea, True, None

        def capture_prompt(context, recent_names, **kwargs):
            mode_calls.update(kwargs)
            return "fake prompt"

        passing_review = MagicMock()
        passing_review.passed = True
        passing_review.reasons = []

        return (
            patch(
                "project_forge.cron.introspect_runner.gather_self_context",
                return_value={
                    "open_issues": [],
                    "recent_commits": [],
                    "test_count": 1,
                    "lint_status": "",
                    "code_stats": {},
                },
            ),
            patch(
                "project_forge.cron.introspect_runner.build_introspection_prompt",
                side_effect=capture_prompt,
            ),
            patch(
                "project_forge.cron.introspect_runner.filter_and_save",
                side_effect=_mock_filter_and_save,
            ),
            patch(
                "project_forge.cron.introspect_runner.review_idea",
                return_value=passing_review,
            ),
        )

    @pytest.mark.asyncio
    async def test_pick_mode_pure_function(self):
        from project_forge.cron.introspect_runner import _pick_introspect_mode

        assert _pick_introspect_mode([]) == "code-fix"
        assert _pick_introspect_mode([self._si_idea("a", None)]) == "code-fix"
        assert _pick_introspect_mode([self._si_idea("a", "introspect-code-fix")]) == "generation"
        assert _pick_introspect_mode([self._si_idea("a", "introspect-generation")]) == "code-fix"

    @pytest.mark.asyncio
    async def test_defaults_to_code_fix_with_no_history(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=self._si_idea("New fix"))

        captured: dict = {}
        with ExitStack() as stack:
            for p in self._patches(captured):
                stack.enter_context(p)
            mock_signals = stack.enter_context(patch("project_forge.cron.introspect_runner.gather_generation_signals"))
            idea = await run_introspect_cycle(mock_db, mock_generator)

        assert captured.get("mode", "code-fix") == "code-fix"
        mock_signals.assert_not_called()
        assert idea.generation_mode == "introspect-code-fix"

    @pytest.mark.asyncio
    async def test_alternates_to_generation_after_code_fix(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[self._si_idea("Prev", "introspect-code-fix")])
        mock_db.save_idea = AsyncMock()
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=self._si_idea("Grounded fix"))

        captured: dict = {}
        sentinel_signals = {"filter_rate_by_category": {"security-tool": 0.9}}
        with ExitStack() as stack:
            for p in self._patches(captured):
                stack.enter_context(p)
            mock_signals = stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.gather_generation_signals",
                    new=AsyncMock(return_value=sentinel_signals),
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.validate_generation_patch",
                    return_value=True,
                )
            )
            idea = await run_introspect_cycle(mock_db, mock_generator)

        mock_signals.assert_awaited_once()
        assert captured["mode"] == "generation"
        assert captured["generation_signals"] == sentinel_signals
        assert idea.generation_mode == "introspect-generation"

    @pytest.mark.asyncio
    async def test_generation_idea_failing_patch_validation_is_dropped(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[self._si_idea("Prev", "introspect-code-fix")])
        mock_db.save_idea = AsyncMock()
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(return_value=self._si_idea("Vague patch"))

        captured: dict = {}
        with ExitStack() as stack:
            for p in self._patches(captured):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.gather_generation_signals",
                    new=AsyncMock(return_value={}),
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.validate_generation_patch",
                    return_value=False,
                )
            )
            idea = await run_introspect_cycle(mock_db, mock_generator)

        assert idea is None
        mock_db.save_idea.assert_not_called()


class TestIntrospectionTournament:
    """Each fire proposes 3 lens candidates; only the best survives (#93)."""

    def _candidate(self, name: str, feasibility: float = 0.8) -> Idea:
        return Idea(
            name=name,
            tagline="a concrete improvement",
            description="A specific fix in src/project_forge/engine/dedup.py with tests to match.",
            category=IdeaCategory.SELF_IMPROVEMENT,
            market_analysis="Target metric: filter rate. Improves generation quality.",
            feasibility_score=feasibility,
            mvp_scope="Patch src/project_forge/engine/dedup.py and add tests.",
            tech_stack=["python"],
        )

    def _review_result(self, score: float, passed: bool = True):
        result = MagicMock()
        result.passed = passed
        result.score = score
        result.reasons = []
        return result

    @pytest.mark.asyncio
    async def test_best_of_three_by_review_score(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        a, b, c = (self._candidate(n) for n in ("Alpha", "Beta", "Gamma"))
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(side_effect=[a, b, c])

        saved = []

        async def _mock_filter_and_save(idea, db):
            saved.append(idea.name)
            return idea, True, None

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.gather_self_context",
                    return_value={"open_issues": [], "recent_commits": [], "test_count": 1},
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.build_introspection_prompt",
                    return_value="fake prompt",
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.review_idea",
                    side_effect=[
                        self._review_result(0.5),
                        self._review_result(0.9),
                        self._review_result(0.7),
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.filter_and_save",
                    side_effect=_mock_filter_and_save,
                )
            )
            winner = await run_introspect_cycle(mock_db, mock_generator)

        assert winner is not None
        assert winner.name == "Beta", "highest review score must win"
        assert saved == ["Beta"], "only the winner reaches the store"
        assert mock_generator.generate.await_count == 3

    @pytest.mark.asyncio
    async def test_lens_injection_varies_code_fix_prompts(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        prompts: list[str] = []

        async def capture_generate(category, prompt_override):
            prompts.append(prompt_override)
            return self._candidate(f"Idea {len(prompts)}")

        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(side_effect=capture_generate)

        async def _mock_filter_and_save(idea, db):
            return idea, True, None

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.gather_self_context",
                    return_value={"open_issues": [], "recent_commits": [], "test_count": 1},
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.build_introspection_prompt",
                    return_value="base prompt",
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.review_idea",
                    return_value=self._review_result(0.8),
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.filter_and_save",
                    side_effect=_mock_filter_and_save,
                )
            )
            await run_introspect_cycle(mock_db, mock_generator)

        assert len(prompts) == 3
        assert len(set(prompts)) == 3, "each candidate must get a distinct lens"
        assert all("lens" in p for p in prompts)

    @pytest.mark.asyncio
    async def test_dupe_winner_falls_through_to_next_survivor(self):
        from project_forge.cron.introspect_runner import run_introspect_cycle
        from project_forge.storage.db import Database

        mock_db = AsyncMock(spec=Database)
        mock_db.list_ideas = AsyncMock(return_value=[])
        mock_db.save_idea = AsyncMock()

        a, b, c = (self._candidate(n) for n in ("Alpha", "Beta", "Gamma"))
        mock_generator = MagicMock()
        mock_generator.generate = AsyncMock(side_effect=[a, b, c])

        calls = []

        async def _dupe_then_accept(idea, db):
            calls.append(idea.name)
            if len(calls) == 1:
                return idea, False, "duplicate:test"
            return idea, True, None

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.gather_self_context",
                    return_value={"open_issues": [], "recent_commits": [], "test_count": 1},
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.build_introspection_prompt",
                    return_value="fake prompt",
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.review_idea",
                    side_effect=[
                        self._review_result(0.9),
                        self._review_result(0.8),
                        self._review_result(0.2),
                    ],
                )
            )
            stack.enter_context(
                patch(
                    "project_forge.cron.introspect_runner.filter_and_save",
                    side_effect=_dupe_then_accept,
                )
            )
            winner = await run_introspect_cycle(mock_db, mock_generator)

        assert calls == ["Alpha", "Beta"], "ranked order: best first, dupe falls through"
        assert winner is not None
        assert winner.name == "Beta"


# --- Empty state message tests ---


class TestEmptyStateMessage:
    """Tests that the empty state shows schedule info."""

    @pytest.mark.asyncio
    async def test_empty_proposals_shows_schedule_info(self, client):
        """When no proposals exist, the empty state should mention the schedule."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        assert resp.status_code == 200
        text = resp.text.lower()
        # Should mention when introspection runs
        assert "daily" in text or "schedule" in text or "hourly" in text or "runs" in text

    @pytest.mark.asyncio
    async def test_empty_proposals_shows_tip(self, client):
        """When no proposals exist, should show a tip or guidance."""
        with patch("project_forge.scaffold.github.list_self_issues", return_value=[]):
            resp = await client.get("/thinktank")
        text = resp.text.lower()
        assert "tip" in text or "introspect" in text or "generate" in text
