"""TDD: SI generation mode — surgical patches to idea-generation logic.

Phase 2 (issue #55). Today, SI patches lint/test bugs only. This phase
adds a generation mode that:

1. gather_generation_signals() — pulls telemetry into a structured dict
2. build_introspection_prompt(..., mode='generation') — emits a prompt that
   forces the LLM to:
   - Touch a file in engine/prompts.py, engine/categories.py,
     engine/super_ideas.py, or engine/router.py
   - Name the metric expected to move
   - Propose ONE hypothesis (not a shotgun)
3. validate_generation_patch(idea) — rejects patches lacking metric
   declaration or touching out-of-scope files
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from project_forge.models import FilteredIdea, Idea, IdeaCategory
from project_forge.storage.db import Database


def _idea(name: str = "Some Idea", *,
          desc: str = "We will edit src/project_forge/engine/prompts.py to do X.",
          mvp: str = "Edit src/project_forge/engine/prompts.py and add tests.",
          market: str = "Target metric: filter_rate[security-tool] should drop.",
          score: float = 0.85) -> Idea:
    return Idea(
        name=name,
        tagline="t",
        description=desc,
        category=IdeaCategory.SELF_IMPROVEMENT,
        market_analysis=market,
        feasibility_score=score,
        mvp_scope=mvp,
        tech_stack=["python"],
    )


def _filtered(name: str = "X", category: IdeaCategory = IdeaCategory.SECURITY_TOOL) -> FilteredIdea:
    fi = FilteredIdea(
        idea_name=name,
        idea_tagline="t",
        idea_category=category,
        filter_reason="duplicate:tagline_similarity:0.9",
        original_idea_json="{}",
    )
    fi.filtered_at = datetime.now(UTC) - timedelta(days=1)
    return fi


@pytest_asyncio.fixture
async def db(tmp_path):
    d = Database(tmp_path / "si_gen.db")
    await d.connect()
    yield d
    await d.close()


# ── gather_generation_signals ────────────────────────────────────────


class TestGatherGenerationSignals:
    @pytest.mark.asyncio
    async def test_returns_dict_with_all_telemetry_keys(self, db):
        from project_forge.engine.introspect import gather_generation_signals

        signals = await gather_generation_signals(db)

        assert "filter_rate_by_category" in signals
        assert "saturation_per_concept" in signals
        assert "novelty_trend" in signals
        assert "diversity_lever_usage" in signals
        assert "coverage_gaps" in signals

    @pytest.mark.asyncio
    async def test_signals_reflect_db_state(self, db):
        from project_forge.engine.introspect import gather_generation_signals

        # Seed a saturation pattern
        for n in ("Certificate A", "Certificate B", "Certificate C"):
            await db.save_filtered_idea(_filtered(n))

        signals = await gather_generation_signals(db)
        sat = signals["saturation_per_concept"]
        names = [c for c, _ in sat]
        assert "certificate" in names


# ── build_introspection_prompt(mode='generation') ────────────────────


class TestGenerationModePrompt:
    def _signals(self):
        return {
            "filter_rate_by_category": {IdeaCategory.SECURITY_TOOL: 0.85},
            "saturation_per_concept": [("certificate", 1357), ("detection", 952)],
            "novelty_trend": [("2026-05-07", 0.91), ("2026-05-08", 0.93)],
            "diversity_lever_usage": {"contrarian": 0.33, "combinatoric": 0.33, "static": 0.34},
            "coverage_gaps": [IdeaCategory.SELF_IMPROVEMENT],
        }

    def test_generation_mode_prompt_includes_telemetry_summary(self):
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = {
            "open_issues": [],
            "recent_commits": ["abc Fix one thing"],
            "test_count": 60,
            "lint_status": "clean",
            "code_stats": {"src": 5000, "tests": 2000},
            "file_tree": ["src/project_forge/engine/prompts.py"],
        }

        prompt = build_introspection_prompt(
            ctx, recent_improvements=[],
            mode="generation",
            generation_signals=self._signals(),
        )

        # Must include saturation token
        assert "certificate" in prompt
        # Must include filter rate
        assert "0.85" in prompt or "85" in prompt
        # Must include novelty trend
        assert "0.91" in prompt or "0.93" in prompt
        # Must include the new constraint
        assert "metric" in prompt.lower()

    def test_generation_mode_constrains_target_files(self):
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = {
            "open_issues": [], "recent_commits": [],
            "test_count": 0, "lint_status": "clean",
            "code_stats": {}, "file_tree": [],
        }

        prompt = build_introspection_prompt(
            ctx, recent_improvements=[],
            mode="generation",
            generation_signals=self._signals(),
        )

        # Must require touching specific generation files
        for path_hint in ("engine/prompts.py", "engine/categories.py",
                          "engine/super_ideas.py", "engine/router.py"):
            assert path_hint in prompt, f"Missing required path hint {path_hint!r}"

    def test_default_mode_is_code_fix_unchanged(self):
        from project_forge.engine.introspect import build_introspection_prompt

        ctx = {
            "open_issues": [], "recent_commits": [],
            "test_count": 0, "lint_status": "clean",
            "code_stats": {}, "file_tree": [],
        }

        # No mode argument → backward-compatible code-fix prompt
        prompt = build_introspection_prompt(ctx, recent_improvements=[])
        # Must not contain telemetry markers
        assert "saturation_per_concept" not in prompt.lower()
        assert "filter_rate" not in prompt.lower()


# ── validate_generation_patch ────────────────────────────────────────


class TestValidateGenerationPatch:
    def test_accepts_well_formed_patch(self):
        from project_forge.engine.introspect import validate_generation_patch

        ok = validate_generation_patch(_idea(
            desc="Edit src/project_forge/engine/prompts.py to inject saturation summary.",
            mvp="src/project_forge/engine/prompts.py + tests/test_prompts.py",
            market="Target metric: filter_rate[security-tool] should drop by 10%.",
        ))
        assert ok, "Well-formed patch must be accepted"

    def test_rejects_patch_without_metric_mention(self):
        from project_forge.engine.introspect import validate_generation_patch

        ok = validate_generation_patch(_idea(
            desc="Edit src/project_forge/engine/prompts.py.",
            mvp="src/project_forge/engine/prompts.py",
            market="This will be cool.",  # no metric named
        ))
        assert not ok, "Patch lacking metric declaration must be rejected"

    def test_rejects_patch_outside_generation_files(self):
        from project_forge.engine.introspect import validate_generation_patch

        ok = validate_generation_patch(_idea(
            desc="Edit src/project_forge/web/app.py to change CSS.",
            mvp="src/project_forge/web/app.py",
            market="Target metric: filter_rate should drop.",  # has metric
        ))
        assert not ok, "Patch on non-generation file must be rejected"

    def test_accepts_super_ideas_target(self):
        from project_forge.engine.introspect import validate_generation_patch

        ok = validate_generation_patch(_idea(
            desc="Edit src/project_forge/engine/super_ideas.py to expand stop words.",
            mvp="engine/super_ideas.py",
            market="Target metric: super_idea_base_collisions should drop.",
        ))
        assert ok
