"""Tests for engine/foundry.py — The Foundry scaffold planner.

Covers:
  - build_scaffold_plan with a stub backend: right shape, no network
  - build_scaffold_plan heuristic fallback (no backend): deterministic result
  - LLM parse failure degrades gracefully to heuristic
  - format_plan_markdown renders expected sections
  - Language detection from tech_stack
  - Repo slug generation
  - Starter issues derived from mvp_scope
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from project_forge.models import Idea, IdeaCategory

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_idea(**overrides) -> Idea:
    base = dict(
        name="Flat Scheduler",
        tagline="self-hosted scheduling at a flat monthly price",
        description=(
            "Calendly is overpriced and enterprise-only. Flat Scheduler is "
            "the open-source, self-hostable alternative at a flat price."
        ),
        category=IdeaCategory.MICRO_SAAS,
        market_analysis="Calendly does $100M+ ARR; OSS challengers pull 30k stars.",
        feasibility_score=0.72,
        mvp_scope=(
            "Phase 1: Freelancer booking page with email notifications. "
            "Phase 2: Team calendars with availability merging. "
            "Phase 3: API and webhook integrations."
        ),
        tech_stack=["typescript", "next.js", "postgres"],
    )
    base.update(overrides)
    return Idea(**base)


def _stub_backend(payload: dict) -> MagicMock:
    """Return a MagicMock that produces json.dumps(payload) on .call()."""
    backend = MagicMock()
    backend.name = "stub:haiku"
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


_VALID_PLAN = {
    "repo_name": "flat-scheduler",
    "description": "Self-hosted scheduling at a flat monthly price",
    "language": "typescript",
    "file_tree": [
        "README.md",
        "package.json",
        "tsconfig.json",
        "src/index.ts",
        "src/lib/core.ts",
        "tests/core.test.ts",
    ],
    "starter_issues": [
        {"title": "Freelancer booking page", "body": "Build the booking UI."},
        {"title": "Email notifications", "body": "Send confirmation emails."},
        {"title": "Team calendar merging", "body": "Merge free/busy signals."},
    ],
    "readme_md": "# flat-scheduler\n\nA flat-price scheduling tool.",
    "first_steps": [
        "Run `npm install`",
        "Run `npm test`",
        "Implement src/lib/core.ts",
    ],
}


# --------------------------------------------------------------------------- #
# build_scaffold_plan — stub backend                                          #
# --------------------------------------------------------------------------- #


class TestBuildScaffoldPlanWithBackend:
    def test_returns_correct_shape_with_backend(self):
        from project_forge.engine.foundry import build_scaffold_plan

        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=_stub_backend(_VALID_PLAN))

        required = {"repo_name", "description", "language", "file_tree", "starter_issues", "readme_md", "first_steps"}
        assert required.issubset(plan.keys())

    def test_uses_llm_values_not_heuristic(self):
        from project_forge.engine.foundry import build_scaffold_plan

        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=_stub_backend(_VALID_PLAN))

        assert plan["repo_name"] == "flat-scheduler"
        assert plan["language"] == "typescript"
        assert len(plan["starter_issues"]) == 3

    def test_strips_json_codefence(self):
        from project_forge.engine.foundry import build_scaffold_plan

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value=f"```json\n{json.dumps(_VALID_PLAN)}\n```")
        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=backend)
        assert plan["repo_name"] == "flat-scheduler"

    def test_strips_generic_codefence(self):
        from project_forge.engine.foundry import build_scaffold_plan

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value=f"```\n{json.dumps(_VALID_PLAN)}\n```")
        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=backend)
        assert plan["language"] == "typescript"

    def test_file_tree_is_a_list(self):
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(), backend=_stub_backend(_VALID_PLAN))
        assert isinstance(plan["file_tree"], list)
        assert len(plan["file_tree"]) >= 3

    def test_starter_issues_have_title_and_body(self):
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(), backend=_stub_backend(_VALID_PLAN))
        for issue in plan["starter_issues"]:
            assert "title" in issue
            assert "body" in issue

    def test_first_steps_is_non_empty_list(self):
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(), backend=_stub_backend(_VALID_PLAN))
        assert isinstance(plan["first_steps"], list)
        assert len(plan["first_steps"]) >= 1

    def test_unknown_language_in_payload_falls_back_to_detected(self):
        from project_forge.engine.foundry import build_scaffold_plan

        payload = dict(_VALID_PLAN, language="cobol")
        plan = build_scaffold_plan(_make_idea(), backend=_stub_backend(payload))
        # idea.tech_stack has typescript so detection should return typescript
        assert plan["language"] == "typescript"


# --------------------------------------------------------------------------- #
# build_scaffold_plan — no backend (heuristic fallback)                      #
# --------------------------------------------------------------------------- #


class TestHeuristicFallback:
    def test_returns_correct_shape_without_backend(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea())
        required = {"repo_name", "description", "language", "file_tree", "starter_issues", "readme_md", "first_steps"}
        assert required.issubset(plan.keys())

    def test_heuristic_detects_typescript_stack(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(tech_stack=["typescript", "next.js"]))
        assert plan["language"] == "typescript"

    def test_heuristic_detects_python_stack(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(tech_stack=["python", "fastapi"]))
        assert plan["language"] == "python"

    def test_heuristic_detects_rust_stack(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(tech_stack=["rust", "tokio", "axum"]))
        assert plan["language"] == "rust"

    def test_heuristic_detects_go_stack(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(tech_stack=["golang", "gin"]))
        assert plan["language"] == "go"

    def test_heuristic_defaults_to_python_on_unknown_stack(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea(tech_stack=["cobol", "fortran"]))
        assert plan["language"] == "python"

    def test_heuristic_file_tree_non_empty(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea())
        assert len(plan["file_tree"]) >= 5
        assert "README.md" in plan["file_tree"]

    def test_heuristic_at_least_3_starter_issues(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea())
        assert len(plan["starter_issues"]) >= 3

    def test_heuristic_at_most_5_starter_issues(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        plan = build_scaffold_plan(_make_idea())
        assert len(plan["starter_issues"]) <= 5

    def test_heuristic_readme_contains_name_and_tagline(self, monkeypatch):
        from project_forge.engine import foundry as foundry_mod

        monkeypatch.setattr(foundry_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.foundry import build_scaffold_plan

        idea = _make_idea()
        plan = build_scaffold_plan(idea)
        assert idea.name in plan["readme_md"]
        assert idea.tagline in plan["readme_md"]


# --------------------------------------------------------------------------- #
# LLM parse failure degrades to heuristic                                    #
# --------------------------------------------------------------------------- #


class TestLLMParseFailure:
    def test_bad_json_falls_back_to_heuristic(self):
        from project_forge.engine.foundry import build_scaffold_plan

        bad = MagicMock()
        bad.name = "stub"
        bad.call = MagicMock(return_value="not valid json at all")

        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=bad)
        # Should still have the right shape from heuristic
        required = {"repo_name", "description", "language", "file_tree", "starter_issues", "readme_md", "first_steps"}
        assert required.issubset(plan.keys())

    def test_missing_keys_falls_back_to_heuristic(self):
        from project_forge.engine.foundry import build_scaffold_plan

        incomplete = {"repo_name": "x", "language": "python"}
        plan = build_scaffold_plan(_make_idea(), backend=_stub_backend(incomplete))
        assert "file_tree" in plan  # heuristic fills it in

    def test_none_return_falls_back_to_heuristic(self):
        from project_forge.engine.foundry import build_scaffold_plan

        backend = MagicMock()
        backend.name = "stub"
        backend.call = MagicMock(return_value=None)

        idea = _make_idea()
        plan = build_scaffold_plan(idea, backend=backend)
        assert "starter_issues" in plan


# --------------------------------------------------------------------------- #
# format_plan_markdown                                                        #
# --------------------------------------------------------------------------- #


class TestFormatPlanMarkdown:
    def test_renders_repo_name_as_h1(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "# flat-scheduler" in md

    def test_renders_description(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "Self-hosted scheduling" in md

    def test_renders_language(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "typescript" in md

    def test_renders_file_tree_section(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "## File Tree" in md
        assert "README.md" in md

    def test_renders_starter_issues_section(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "## Starter Issues" in md
        assert "Freelancer booking page" in md

    def test_renders_first_steps_section(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "## First Steps" in md
        assert "npm install" in md

    def test_renders_readme_preview_section(self):
        from project_forge.engine.foundry import format_plan_markdown

        md = format_plan_markdown(_VALID_PLAN)
        assert "## README Preview" in md
        assert "flat-scheduler" in md

    def test_returns_string(self):
        from project_forge.engine.foundry import format_plan_markdown

        result = format_plan_markdown(_VALID_PLAN)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_handles_empty_plan_gracefully(self):
        from project_forge.engine.foundry import format_plan_markdown

        # Should not raise — empty dicts produce sensible empty output.
        result = format_plan_markdown({})
        assert isinstance(result, str)


# --------------------------------------------------------------------------- #
# _detect_language unit tests                                                 #
# --------------------------------------------------------------------------- #


class TestDetectLanguage:
    def test_typescript_wins_on_next_js(self):
        from project_forge.engine.foundry import _detect_language

        assert _detect_language(["next.js", "tailwind"]) == "typescript"

    def test_rust_detected(self):
        from project_forge.engine.foundry import _detect_language

        assert _detect_language(["rust", "tokio"]) == "rust"

    def test_go_detected(self):
        from project_forge.engine.foundry import _detect_language

        assert _detect_language(["golang", "chi"]) == "go"

    def test_python_detected(self):
        from project_forge.engine.foundry import _detect_language

        assert _detect_language(["fastapi", "pydantic"]) == "python"

    def test_empty_stack_defaults_to_python(self):
        from project_forge.engine.foundry import _detect_language

        assert _detect_language([]) == "python"


# --------------------------------------------------------------------------- #
# _repo_slug unit tests                                                       #
# --------------------------------------------------------------------------- #


class TestRepoSlug:
    def test_spaces_become_hyphens(self):
        from project_forge.engine.foundry import _repo_slug

        assert _repo_slug("Flat Scheduler") == "flat-scheduler"

    def test_special_chars_stripped(self):
        from project_forge.engine.foundry import _repo_slug

        assert _repo_slug("AI-Powered (Tool!)") == "ai-powered-tool"

    def test_truncated_at_80(self):
        from project_forge.engine.foundry import _repo_slug

        long_name = "a" * 100
        assert len(_repo_slug(long_name)) <= 80

    def test_empty_input_returns_default(self):
        from project_forge.engine.foundry import _repo_slug

        assert _repo_slug("") == "forge-idea"


# --------------------------------------------------------------------------- #
# _issues_from_mvp unit tests                                                #
# --------------------------------------------------------------------------- #


class TestIssuesFromMvp:
    def test_extracts_at_least_3(self):
        from project_forge.engine.foundry import _issues_from_mvp

        issues = _issues_from_mvp("Build login. Add dashboard. Write tests.")
        assert len(issues) >= 3

    def test_at_most_5(self):
        from project_forge.engine.foundry import _issues_from_mvp

        scope = ". ".join([f"Step {i}" * 10 for i in range(10)])
        issues = _issues_from_mvp(scope)
        assert len(issues) <= 5

    def test_empty_scope_uses_defaults(self):
        from project_forge.engine.foundry import _issues_from_mvp

        issues = _issues_from_mvp("")
        assert len(issues) >= 3
        assert all("title" in i and "body" in i for i in issues)

    def test_issues_have_title_and_body(self):
        from project_forge.engine.foundry import _issues_from_mvp

        issues = _issues_from_mvp("Phase 1: booking. Phase 2: payments.")
        for issue in issues:
            assert "title" in issue
            assert "body" in issue
            assert issue["title"]
