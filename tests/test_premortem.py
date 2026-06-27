"""Tests for the Kill Board pre-mortem scorer (engine/premortem.py).

Strategy:
  - heuristic is cheap + deterministic — test the full signal surface.
  - score_survival: monkeypatch resolve_cheap_backend; no real LLM.
  - generate_premortem: inject a stub backend via keyword arg; no network.
  - All assertions use only the public API.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from project_forge.models import Idea, IdeaCategory

# ── Helpers ──────────────────────────────────────────────────────────────── #


def _idea(**overrides) -> Idea:
    """Return a well-formed idea with sensible defaults."""
    base = dict(
        name="SecureScan",
        tagline="developer-first vulnerability scanner with zero false positives",
        description=(
            "SecureScan is an open-source SAST tool aimed at replacing "
            "Snyk and Semgrep for teams that need a self-hosted option. "
            "Driven by tree-sitter AST analysis, it targets the SMB market "
            "that cannot afford $50k/year enterprise contracts. The tool "
            "has 1200 GitHub stars and 40 paying beta customers generating "
            "$3k MRR after 6 weeks. Snyk recently raised prices 30% after "
            "their PE acquisition, pushing teams to look for alternatives."
        ),
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis=(
            "SAST market is $2B+; Snyk is a unicorn doing $200M+ ARR. "
            "OSS challengers (Semgrep, CodeQL) have 30k+ stars and prove "
            "developer appetite. SMB segment is $400M+ underserved."
        ),
        feasibility_score=0.78,
        mvp_scope=(
            "Phase 1: Python + JS scanners with 50 core rules. Phase 2: GitHub Actions integration. Phase 3: dashboard."
        ),
        tech_stack=["python", "rust", "tree-sitter", "postgres"],
        target_incumbent="Snyk",
    )
    base.update(overrides)
    return Idea(**base)


def _vague_idea(**overrides) -> Idea:
    """Return a buzzwordy, underdeveloped idea."""
    base = dict(
        name="RevoluTech",
        tagline="revolutionary paradigm shift for the next generation",
        description="This groundbreaking, game-changing, world-class solution is unprecedented.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="People might pay for this.",
        feasibility_score=0.30,
        mvp_scope="build it",
        tech_stack=[],
    )
    base.update(overrides)
    return Idea(**base)


def _stub_backend(payload: dict, name: str = "stub:haiku") -> MagicMock:
    backend = MagicMock()
    backend.name = name
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


@pytest.fixture(autouse=True)
def _no_real_backend(monkeypatch):
    """Force the heuristic path by default so `backend=None` tests never reach
    the real `claude` CLI (which is on PATH in this environment and would make
    slow live LLM calls). Tests that set their own backend/monkeypatch override
    this — their explicit setup runs after this fixture.
    """
    import project_forge.engine.premortem as pm_mod

    monkeypatch.setattr(pm_mod, "resolve_cheap_backend", lambda: None)


# ── score_survival_heuristic ─────────────────────────────────────────────── #


class TestSurvivalHeuristic:
    def test_result_always_in_unit_interval(self):
        from project_forge.engine.premortem import score_survival_heuristic

        assert 0.0 <= score_survival_heuristic(_idea()) <= 1.0
        assert 0.0 <= score_survival_heuristic(_vague_idea()) <= 1.0

    def test_strong_idea_scores_higher_than_vague(self):
        from project_forge.engine.premortem import score_survival_heuristic

        strong = score_survival_heuristic(_idea())
        vague = score_survival_heuristic(_vague_idea())
        assert strong > vague, f"strong={strong:.3f} should exceed vague={vague:.3f}"

    def test_strong_idea_scores_above_midpoint(self):
        from project_forge.engine.premortem import score_survival_heuristic

        # A well-grounded idea should score above 0.50.
        assert score_survival_heuristic(_idea()) > 0.50

    def test_vague_idea_scores_below_midpoint(self):
        from project_forge.engine.premortem import score_survival_heuristic

        assert score_survival_heuristic(_vague_idea()) < 0.50

    def test_high_feasibility_raises_score(self):
        from project_forge.engine.premortem import score_survival_heuristic

        low = score_survival_heuristic(_idea(feasibility_score=0.1))
        high = score_survival_heuristic(_idea(feasibility_score=0.9))
        assert high > low

    def test_named_incumbent_raises_score(self):
        from project_forge.engine.premortem import score_survival_heuristic

        named = score_survival_heuristic(_idea(target_incumbent="Snyk"))
        unnamed = score_survival_heuristic(_idea(target_incumbent=None))
        assert named > unnamed

    def test_empty_tech_stack_lowers_score(self):
        from project_forge.engine.premortem import score_survival_heuristic

        with_stack = score_survival_heuristic(_idea())
        without = score_survival_heuristic(_idea(tech_stack=[]))
        assert with_stack > without

    def test_buzzword_density_lowers_score(self):
        from project_forge.engine.premortem import score_survival_heuristic

        # Five distinct buzzwords injected into description.
        buzzword_desc = (
            "A revolutionary, groundbreaking, unprecedented, game-changing, "
            "world-class solution that is cutting-edge and state-of-the-art."
        )
        buzzy = score_survival_heuristic(_idea(description=buzzword_desc, market_analysis="None."))
        clean = score_survival_heuristic(_idea())
        assert clean > buzzy

    def test_vague_mvp_scope_lowers_score(self):
        from project_forge.engine.premortem import score_survival_heuristic

        vague_mvp = score_survival_heuristic(_idea(mvp_scope="build it"))
        concrete_mvp = score_survival_heuristic(
            _idea(mvp_scope="Phase 1: ship core scanner. Phase 2: GitHub integration.")
        )
        assert concrete_mvp > vague_mvp

    def test_short_description_penalised(self):
        from project_forge.engine.premortem import score_survival_heuristic

        short = score_survival_heuristic(_idea(description="Too short."))
        long_ = score_survival_heuristic(_idea())  # default is long
        assert long_ > short


# ── score_survival (async + LLM tie-break) ──────────────────────────────── #


class TestScoreSurvival:
    @pytest.mark.asyncio
    async def test_no_backend_returns_heuristic(self, monkeypatch):
        import project_forge.engine.premortem as pm_mod

        monkeypatch.setattr(pm_mod, "resolve_cheap_backend", lambda: None)
        from project_forge.engine.premortem import score_survival, score_survival_heuristic

        idea = _idea()
        result = await score_survival(idea)
        expected = score_survival_heuristic(idea)
        assert result == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_out_of_band_high_skips_llm(self, monkeypatch):
        """Heuristic above LLM_VERIFY_UPPER should be returned as-is."""
        import project_forge.engine.premortem as pm_mod

        called = {"n": 0}

        def fake_backend():
            called["n"] += 1
            return None

        monkeypatch.setattr(pm_mod, "resolve_cheap_backend", fake_backend)
        from project_forge.engine.premortem import LLM_VERIFY_UPPER, score_survival

        # Give a very strong idea that scores above the band.
        idea = _idea(feasibility_score=0.99)
        result = await score_survival(idea)
        assert 0.0 <= result <= 1.0
        # If the heuristic is above LLM_VERIFY_UPPER, the backend is never called.
        from project_forge.engine.premortem import score_survival_heuristic

        h = score_survival_heuristic(idea)
        if h > LLM_VERIFY_UPPER:
            assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_llm_response_clamped_to_unit(self, monkeypatch):
        """Even if the LLM returns an out-of-range value, we clamp it."""
        import project_forge.engine.premortem as pm_mod

        # Return survival_odds = 2.5 (out of range).
        backend = _stub_backend({"survival_odds": 2.5})
        monkeypatch.setattr(pm_mod, "resolve_cheap_backend", lambda: backend)
        from project_forge.engine.premortem import score_survival

        # Use a vague idea so the heuristic lands in the borderline band.
        idea = _vague_idea(feasibility_score=0.5, description="Moderate length " * 10)
        result = await score_survival(idea)
        assert 0.0 <= result <= 1.0

    @pytest.mark.asyncio
    async def test_bad_llm_json_falls_back_to_heuristic(self, monkeypatch):
        import project_forge.engine.premortem as pm_mod

        bad_backend = MagicMock()
        bad_backend.call = MagicMock(return_value="not valid json at all")
        monkeypatch.setattr(pm_mod, "resolve_cheap_backend", lambda: bad_backend)
        from project_forge.engine.premortem import score_survival, score_survival_heuristic

        idea = _vague_idea(feasibility_score=0.5, description="Moderate " * 15)
        result = await score_survival(idea)
        h = score_survival_heuristic(idea)
        assert result == pytest.approx(h)


# ── generate_premortem ───────────────────────────────────────────────────── #


_PREMORTEM_PAYLOAD = {
    "case_against": (
        "SecureScan enters a market dominated by Snyk, Semgrep, and CodeQL, "
        "all of which have 10-100× the engineering headcount and years of rule "
        "development. The 'open-source' angle has already been executed by "
        "Semgrep (100k+ users). There is no evidence of a structural wedge "
        "beyond price, which is a race to the bottom."
    ),
    "whos_already_doing_it": ["Snyk", "Semgrep", "CodeQL", "Sonarqube", "Bearer"],
    "why_now_wrong": (
        "The SAST market has consolidated around a few winners with strong "
        "network effects from their rule ecosystems. Entering now requires "
        "either a 10× technical breakthrough or massive marketing spend that "
        "a bootstrapped SMB tool cannot sustain."
    ),
    "fatal_risks": [
        "Rule ecosystem cold-start: no rules → no users → no contributors.",
        "False-positive rate must be near-zero or developers will disable it.",
        "GitHub's free Dependabot competes in the same budget line.",
    ],
    "survival_odds": 0.28,
}


class TestGeneratePremortem:
    @pytest.mark.asyncio
    async def test_returns_correct_shape_with_stub_backend(self):
        from project_forge.engine.premortem import generate_premortem

        backend = _stub_backend(_PREMORTEM_PAYLOAD)
        result = await generate_premortem(_idea(), backend=backend)

        assert isinstance(result, dict)
        assert "case_against" in result
        assert "whos_already_doing_it" in result
        assert "why_now_wrong" in result
        assert "fatal_risks" in result
        assert "survival_odds" in result

    @pytest.mark.asyncio
    async def test_survival_odds_in_unit_interval(self):
        from project_forge.engine.premortem import generate_premortem

        backend = _stub_backend(_PREMORTEM_PAYLOAD)
        result = await generate_premortem(_idea(), backend=backend)
        assert 0.0 <= result["survival_odds"] <= 1.0

    @pytest.mark.asyncio
    async def test_whos_already_doing_it_is_list(self):
        from project_forge.engine.premortem import generate_premortem

        backend = _stub_backend(_PREMORTEM_PAYLOAD)
        result = await generate_premortem(_idea(), backend=backend)
        assert isinstance(result["whos_already_doing_it"], list)
        assert len(result["whos_already_doing_it"]) > 0
        assert "Snyk" in result["whos_already_doing_it"]

    @pytest.mark.asyncio
    async def test_fatal_risks_is_list(self):
        from project_forge.engine.premortem import generate_premortem

        backend = _stub_backend(_PREMORTEM_PAYLOAD)
        result = await generate_premortem(_idea(), backend=backend)
        assert isinstance(result["fatal_risks"], list)
        assert len(result["fatal_risks"]) >= 1

    @pytest.mark.asyncio
    async def test_case_against_is_nonempty_string(self):
        from project_forge.engine.premortem import generate_premortem

        backend = _stub_backend(_PREMORTEM_PAYLOAD)
        result = await generate_premortem(_idea(), backend=backend)
        assert isinstance(result["case_against"], str)
        assert len(result["case_against"]) > 10

    @pytest.mark.asyncio
    async def test_no_backend_returns_heuristic_fallback(self):
        from project_forge.engine.premortem import generate_premortem

        result = await generate_premortem(_idea(), backend=None)
        # Shape must be correct even without LLM.
        assert "case_against" in result
        assert "whos_already_doing_it" in result
        assert "why_now_wrong" in result
        assert "fatal_risks" in result
        assert "survival_odds" in result
        assert 0.0 <= result["survival_odds"] <= 1.0

    @pytest.mark.asyncio
    async def test_unparseable_llm_response_falls_back(self):
        from project_forge.engine.premortem import generate_premortem

        bad = MagicMock()
        bad.call = MagicMock(return_value="this is not JSON")
        result = await generate_premortem(_idea(), backend=bad)
        # Falls back to heuristic — shape must still be valid.
        assert "case_against" in result
        assert 0.0 <= result["survival_odds"] <= 1.0

    @pytest.mark.asyncio
    async def test_codefence_stripped_before_parse(self):
        from project_forge.engine.premortem import generate_premortem

        wrapped = "```json\n" + json.dumps(_PREMORTEM_PAYLOAD) + "\n```"
        backend = MagicMock()
        backend.call = MagicMock(return_value=wrapped)
        result = await generate_premortem(_idea(), backend=backend)
        assert result["survival_odds"] == pytest.approx(0.28)

    @pytest.mark.asyncio
    async def test_survival_odds_clamped_when_llm_out_of_range(self):
        from project_forge.engine.premortem import generate_premortem

        payload = dict(_PREMORTEM_PAYLOAD)
        payload["survival_odds"] = 5.0  # out of range
        backend = _stub_backend(payload)
        result = await generate_premortem(_idea(), backend=backend)
        assert result["survival_odds"] <= 1.0

    @pytest.mark.asyncio
    async def test_competitors_list_capped_at_eight(self):
        from project_forge.engine.premortem import generate_premortem

        payload = dict(_PREMORTEM_PAYLOAD)
        payload["whos_already_doing_it"] = [f"Comp{i}" for i in range(20)]
        backend = _stub_backend(payload)
        result = await generate_premortem(_idea(), backend=backend)
        assert len(result["whos_already_doing_it"]) <= 8

    @pytest.mark.asyncio
    async def test_fatal_risks_capped_at_five(self):
        from project_forge.engine.premortem import generate_premortem

        payload = dict(_PREMORTEM_PAYLOAD)
        payload["fatal_risks"] = [f"Risk {i}" for i in range(10)]
        backend = _stub_backend(payload)
        result = await generate_premortem(_idea(), backend=backend)
        assert len(result["fatal_risks"]) <= 5

    @pytest.mark.asyncio
    async def test_vague_idea_fallback_has_risks_listed(self):
        from project_forge.engine.premortem import generate_premortem

        result = await generate_premortem(_vague_idea(), backend=None)
        assert len(result["fatal_risks"]) >= 1

    @pytest.mark.asyncio
    async def test_strong_idea_higher_survival_than_vague_in_fallback(self):
        from project_forge.engine.premortem import generate_premortem

        strong = await generate_premortem(_idea(), backend=None)
        vague = await generate_premortem(_vague_idea(), backend=None)
        assert strong["survival_odds"] > vague["survival_odds"]


# ── _strip_codefence helper ──────────────────────────────────────────────── #


class TestStripCodefence:
    def test_strips_json_fence(self):
        from project_forge.engine.premortem import _strip_codefence

        raw = '```json\n{"k": 1}\n```'
        assert _strip_codefence(raw) == '{"k": 1}'

    def test_strips_plain_fence(self):
        from project_forge.engine.premortem import _strip_codefence

        raw = '```\n{"k": 1}\n```'
        assert _strip_codefence(raw) == '{"k": 1}'

    def test_no_fence_unchanged(self):
        from project_forge.engine.premortem import _strip_codefence

        raw = '{"k": 1}'
        assert _strip_codefence(raw) == raw
