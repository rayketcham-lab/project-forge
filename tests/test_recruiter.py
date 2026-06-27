"""Tests for THE RECRUITER — autonomous build-cost estimator.

Test strategy:
  - heuristic: deterministic, no network, no LLM
  - LLM path: backend injected via MagicMock (JSON stub)
  - fallback: bad JSON and invalid-shape responses both revert to heuristic
  - markdown: basic render checks — table header, all roles, totals, skills
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from project_forge.models import Idea, IdeaCategory

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _make_idea(**over) -> Idea:
    base = dict(
        name="TestProject",
        tagline="A useful automation tool",
        description="Automates repetitive workflows so teams can focus on real work.",
        category=IdeaCategory.AUTOMATION,
        market_analysis="Many teams waste hours on manual repetition.",
        feasibility_score=0.7,
        mvp_scope=("Phase 1: core automation engine. Phase 2: dashboard UI. Phase 3: integrations marketplace."),
        tech_stack=["python", "fastapi", "postgres"],
    )
    base.update(over)
    return Idea(**base)


def _stub_backend(payload: dict) -> MagicMock:
    """Return a MagicMock backend that returns *payload* serialised as JSON."""
    backend = MagicMock()
    backend.name = "stub:haiku"
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


_VALID_PAYLOAD: dict = {
    "roles": [
        {"role": "Backend Engineer", "count": 2, "weeks": 16},
        {"role": "Frontend Engineer", "count": 1, "weeks": 12},
        {"role": "DevOps Engineer", "count": 1, "weeks": 8},
    ],
    "total_person_weeks": 52,
    "skills": ["Python", "React", "Docker", "SQL"],
    "cost_band": "$75k–$150k",
    "complexity": 3,
    "timeline_weeks": 16,
}


# --------------------------------------------------------------------------- #
# Complexity heuristic                                                         #
# --------------------------------------------------------------------------- #


class TestComplexityHeuristic:
    def test_output_in_range(self):
        from project_forge.engine.recruiter import _infer_complexity

        idea = _make_idea()
        assert 1 <= _infer_complexity(idea) <= 5

    def test_larger_stack_never_lower_complexity(self):
        from project_forge.engine.recruiter import _infer_complexity

        small = _make_idea(tech_stack=["python"])
        big = _make_idea(
            tech_stack=["python", "rust", "go", "typescript", "k8s", "terraform", "postgres", "redis"],
        )
        assert _infer_complexity(big) >= _infer_complexity(small)

    def test_low_feasibility_raises_complexity(self):
        from project_forge.engine.recruiter import _infer_complexity

        hard = _make_idea(feasibility_score=0.10)
        easy = _make_idea(feasibility_score=0.95)
        assert _infer_complexity(hard) >= _infer_complexity(easy)

    def test_longer_scope_raises_complexity(self):
        from project_forge.engine.recruiter import _infer_complexity

        brief = _make_idea(mvp_scope="Build it.")
        detailed = _make_idea(
            mvp_scope=(
                "Phase 1: user auth, core API, basic UI, data model, CI pipeline. "
                "Phase 2: payments, notifications, admin panel, audit log. "
                "Phase 3: mobile app, analytics, marketplace, third-party integrations. "
                "Phase 4: enterprise SSO, RBAC, SLA, SOC2, pen-test remediation."
            ),
        )
        assert _infer_complexity(detailed) >= _infer_complexity(brief)

    def test_boundary_complexity_one_for_trivial(self):
        """A tiny, highly-feasible idea with a single tech should land near 1."""
        from project_forge.engine.recruiter import _infer_complexity

        trivial = _make_idea(
            tech_stack=["python"],
            mvp_scope="Ship it.",
            feasibility_score=0.99,
        )
        assert _infer_complexity(trivial) == 1

    def test_boundary_complexity_high_for_massive(self):
        """A large, low-feasibility idea should land at complexity >= 4."""
        from project_forge.engine.recruiter import _infer_complexity

        massive = _make_idea(
            tech_stack=["python", "rust", "go", "typescript", "k8s", "terraform", "postgres", "redis", "kafka", "grpc"],
            mvp_scope=(
                "Phase 1: distributed ingestion pipeline with exactly-once semantics, "
                "autoscaling, multi-region replication, and consensus protocol. "
                "Phase 2: real-time ML inference layer, feature store, A/B testing. "
                "Phase 3: compliance, audit, SOC2, GDPR, pen-testing, security audit."
            ),
            feasibility_score=0.05,
        )
        assert _infer_complexity(massive) >= 4


# --------------------------------------------------------------------------- #
# Heuristic estimate shape                                                     #
# --------------------------------------------------------------------------- #


class TestHeuristicEstimate:
    def test_shape_complete(self):
        from project_forge.engine.recruiter import _heuristic_estimate

        est = _heuristic_estimate(_make_idea())
        assert "roles" in est
        assert "total_person_weeks" in est
        assert "skills" in est
        assert "cost_band" in est
        assert "complexity" in est
        assert "timeline_weeks" in est

    def test_sane_numbers(self):
        from project_forge.engine.recruiter import _heuristic_estimate

        est = _heuristic_estimate(_make_idea())
        assert 1 <= est["complexity"] <= 5
        assert est["total_person_weeks"] > 0
        assert est["timeline_weeks"] > 0
        assert est["cost_band"]

    def test_roles_not_empty_across_all_complexities(self):
        from project_forge.engine.recruiter import _build_from_complexity

        for c in range(1, 6):
            est = _build_from_complexity(c, [])
            assert len(est["roles"]) >= 1
            for r in est["roles"]:
                assert r["count"] >= 1
                assert r["weeks"] >= 1

    def test_total_person_weeks_matches_roles(self):
        from project_forge.engine.recruiter import _heuristic_estimate

        est = _heuristic_estimate(_make_idea())
        computed = sum(r["count"] * r["weeks"] for r in est["roles"])
        assert est["total_person_weeks"] == computed

    def test_idea_tech_stack_appended_to_skills(self):
        from project_forge.engine.recruiter import _heuristic_estimate

        idea = _make_idea(tech_stack=["exotic-db", "custom-protocol"])
        est = _heuristic_estimate(idea)
        skill_names_lower = [s.lower() for s in est["skills"]]
        assert "exotic-db" in skill_names_lower
        assert "custom-protocol" in skill_names_lower

    def test_cost_band_not_empty(self):
        from project_forge.engine.recruiter import _cost_band

        assert _cost_band(5) == "$10k–$30k"
        assert _cost_band(10) == "$10k–$30k"
        assert _cost_band(11) == "$30k–$75k"
        assert _cost_band(101) == "$350k+"


# --------------------------------------------------------------------------- #
# LLM path                                                                     #
# --------------------------------------------------------------------------- #


class TestLLMPath:
    def test_stub_backend_result_used(self):
        from project_forge.engine.recruiter import estimate_build

        est = estimate_build(_make_idea(), backend=_stub_backend(_VALID_PAYLOAD))
        assert est["complexity"] == 3
        assert est["timeline_weeks"] == 16
        assert len(est["roles"]) == 3

    def test_bad_json_falls_back_to_heuristic(self):
        from project_forge.engine.recruiter import _heuristic_estimate, estimate_build

        bad = MagicMock()
        bad.name = "stub"
        bad.call = MagicMock(return_value="not json at all")
        idea = _make_idea()
        est = estimate_build(idea, backend=bad)
        heuristic = _heuristic_estimate(idea)
        assert est["complexity"] == heuristic["complexity"]
        assert est["total_person_weeks"] == heuristic["total_person_weeks"]

    def test_invalid_shape_falls_back_to_heuristic(self):
        from project_forge.engine.recruiter import _heuristic_estimate, estimate_build

        # Payload is valid JSON but missing required fields.
        partial = {"complexity": 3}
        idea = _make_idea()
        est = estimate_build(idea, backend=_stub_backend(partial))
        heuristic = _heuristic_estimate(idea)
        assert est["complexity"] == heuristic["complexity"]

    def test_complexity_out_of_range_falls_back(self):
        from project_forge.engine.recruiter import _heuristic_estimate, estimate_build

        bad_complexity = dict(_VALID_PAYLOAD, complexity=99)
        idea = _make_idea()
        est = estimate_build(idea, backend=_stub_backend(bad_complexity))
        heuristic = _heuristic_estimate(idea)
        assert est["complexity"] == heuristic["complexity"]

    def test_missing_roles_falls_back(self):
        from project_forge.engine.recruiter import _heuristic_estimate, estimate_build

        no_roles = dict(_VALID_PAYLOAD, roles=[])
        idea = _make_idea()
        est = estimate_build(idea, backend=_stub_backend(no_roles))
        heuristic = _heuristic_estimate(idea)
        assert est["total_person_weeks"] == heuristic["total_person_weeks"]

    def test_no_backend_resolves_uses_heuristic(self, monkeypatch):
        import project_forge.engine.recruiter as recruiter_mod

        monkeypatch.setattr(recruiter_mod, "resolve_cheap_backend", lambda: None)
        idea = _make_idea()
        est = recruiter_mod.estimate_build(idea)
        assert 1 <= est["complexity"] <= 5
        assert est["total_person_weeks"] > 0

    def test_codefence_json_stripped_and_parsed(self):
        from project_forge.engine.recruiter import _strip_codefence

        fenced = f"```json\n{json.dumps(_VALID_PAYLOAD)}\n```"
        cleaned = _strip_codefence(fenced)
        parsed = json.loads(cleaned)
        assert parsed["complexity"] == 3

    def test_plain_backtick_fence_stripped(self):
        from project_forge.engine.recruiter import _strip_codefence

        fenced = f"```\n{json.dumps(_VALID_PAYLOAD)}\n```"
        cleaned = _strip_codefence(fenced)
        parsed = json.loads(cleaned)
        assert parsed["timeline_weeks"] == 16

    def test_codefence_backend_response_accepted(self):
        """Backend that wraps JSON in ```json fences should still be accepted."""
        from project_forge.engine.recruiter import estimate_build

        backend = MagicMock()
        backend.name = "stub:fenced"
        backend.call = MagicMock(return_value=f"```json\n{json.dumps(_VALID_PAYLOAD)}\n```")
        est = estimate_build(_make_idea(), backend=backend)
        assert est["complexity"] == 3


# --------------------------------------------------------------------------- #
# Markdown rendering                                                           #
# --------------------------------------------------------------------------- #


class TestMarkdownFormat:
    def test_renders_header_line(self):
        from project_forge.engine.recruiter import _heuristic_estimate, format_estimate_markdown

        md = format_estimate_markdown(_heuristic_estimate(_make_idea()))
        assert "Complexity" in md
        assert "Timeline" in md
        assert "Cost band" in md

    def test_renders_role_table(self):
        from project_forge.engine.recruiter import format_estimate_markdown

        md = format_estimate_markdown(_VALID_PAYLOAD)
        assert "| Role |" in md
        assert "Backend Engineer" in md
        assert "Frontend Engineer" in md

    def test_renders_total_and_skills(self):
        from project_forge.engine.recruiter import format_estimate_markdown

        md = format_estimate_markdown(_VALID_PAYLOAD)
        assert "Total person-weeks" in md
        assert "52" in md
        assert "Skills needed" in md
        assert "Python" in md

    def test_all_roles_present_in_output(self):
        from project_forge.engine.recruiter import format_estimate_markdown

        md = format_estimate_markdown(_VALID_PAYLOAD)
        for r in _VALID_PAYLOAD["roles"]:
            assert r["role"] in md

    def test_heuristic_markdown_roundtrip(self):
        from project_forge.engine.recruiter import _heuristic_estimate, format_estimate_markdown

        for feasibility in [0.1, 0.5, 0.9]:
            est = _heuristic_estimate(_make_idea(feasibility_score=feasibility))
            md = format_estimate_markdown(est)
            assert str(est["complexity"]) in md
            assert str(est["timeline_weeks"]) in md
