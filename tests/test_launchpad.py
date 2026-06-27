"""Tests for engine/launchpad.py — autonomous GTM brief generator.

Covers:
  - Stub backend → full brief with correct shape and all required keys.
  - No-backend fallback → heuristic brief is structurally valid.
  - JSON with codefence wrappers is stripped correctly.
  - Missing/malformed LLM response degrades to heuristic.
  - format_brief_markdown renders all sections.
  - Category-specific ICP and channel hints appear in the heuristic brief.

No network calls. No real LLM. All backends are MagicMock.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from project_forge.models import Idea, IdeaCategory

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

_REQUIRED_KEYS = frozenset(
    {
        "positioning",
        "icp",
        "first_ten_customers",
        "channels",
        "pricing",
        "landing_headline",
        "cold_open",
        "launch_checklist",
    }
)


def _make_idea(**overrides) -> Idea:
    base = dict(
        name="RateGuard",
        tagline="real-time API rate-limit dashboard for SREs",
        description=(
            "SREs lose hours firefighting rate-limit incidents because existing APM tools "
            "bury the signal. RateGuard surfaces rate-limit exhaustion before it pages you, "
            "with a live heatmap of all upstream API quotas and automatic runbook triggers."
        ),
        category=IdeaCategory.OBSERVABILITY,
        market_analysis=(
            "Datadog charges $15/host/mo and doesn't expose per-API-key quota burns; "
            "teams build ad-hoc scripts. $500M+ APM market with no quota-native tool."
        ),
        feasibility_score=0.72,
        mvp_scope="Phase 1: webhook ingestion + live quota heatmap. Phase 2: runbook triggers.",
        tech_stack=["python", "fastapi", "postgres", "redis"],
    )
    base.update(overrides)
    return Idea(**base)


def _stub_backend(payload: dict, name: str = "stub:haiku") -> MagicMock:
    backend = MagicMock()
    backend.name = name
    backend.call = MagicMock(return_value=json.dumps(payload))
    return backend


_FULL_PAYLOAD: dict = {
    "positioning": "For SREs who lose hours to rate-limit fires, RateGuard is the only quota-native APM.",
    "icp": "SRE at a B2B SaaS company with 5+ upstream API dependencies and an on-call rotation.",
    "first_ten_customers": [
        "Post Show HN: RateGuard — real-time API quota heatmap.",
        "DM 10 SREs who have tweeted about rate-limit pain.",
        "Offer free 30-day access for a 15-minute call.",
        "Ask each user for one peer referral in exchange for a lifetime discount.",
        "Write a case study after each early win.",
    ],
    "channels": ["Hacker News", "SRE Slack communities", "Twitter/X"],
    "pricing": "Free 3-host tier; $49/mo up to 25 hosts; $149/mo unlimited.",
    "landing_headline": "Stop firefighting rate limits. See every API quota before it pages you.",
    "cold_open": (
        "Hi — I'm building RateGuard, a rate-limit dashboard for SREs. "
        "I saw your tweet about rate-limit incidents — would a quick demo help?"
    ),
    "launch_checklist": [
        "Set up landing page with email capture",
        "Build webhook ingestion MVP",
        "Record a 90-second Loom demo",
        "Get 5 strangers to critique the landing",
        "Put pricing live on day 1",
    ],
}


# --------------------------------------------------------------------------- #
# generate_gtm_brief — stub backend                                           #
# --------------------------------------------------------------------------- #


class TestGenerateGtmBriefWithBackend:
    def test_returns_all_required_keys(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(_FULL_PAYLOAD))
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))

    def test_list_fields_are_lists(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(_FULL_PAYLOAD))
        assert isinstance(brief["first_ten_customers"], list)
        assert isinstance(brief["channels"], list)
        assert isinstance(brief["launch_checklist"], list)

    def test_string_fields_are_non_empty_strings(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(_FULL_PAYLOAD))
        for key in ("positioning", "icp", "pricing", "landing_headline", "cold_open"):
            assert isinstance(brief[key], str)
            assert brief[key].strip()

    def test_backend_name_stored_in_brief(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(_FULL_PAYLOAD, "stub:haiku"))
        assert brief.get("_backend") == "stub:haiku"

    def test_preserves_llm_content(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(_FULL_PAYLOAD))
        assert "quota" in brief["positioning"].lower() or "SRE" in brief["positioning"]
        assert brief["pricing"] == _FULL_PAYLOAD["pricing"]

    def test_codefence_json_is_stripped(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        wrapped = f"```json\n{json.dumps(_FULL_PAYLOAD)}\n```"
        backend = MagicMock()
        backend.name = "stub:haiku"
        backend.call = MagicMock(return_value=wrapped)

        brief = generate_gtm_brief(_make_idea(), backend=backend)
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))

    def test_plain_codefence_is_stripped(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        wrapped = f"```\n{json.dumps(_FULL_PAYLOAD)}\n```"
        backend = MagicMock()
        backend.name = "stub:haiku"
        backend.call = MagicMock(return_value=wrapped)

        brief = generate_gtm_brief(_make_idea(), backend=backend)
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))


# --------------------------------------------------------------------------- #
# generate_gtm_brief — degradation paths                                      #
# --------------------------------------------------------------------------- #


class TestGenerateGtmBriefDegradation:
    def test_no_backend_returns_heuristic_brief(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        brief = lp.generate_gtm_brief(_make_idea())
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))

    def test_heuristic_brief_list_fields_non_empty(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        brief = lp.generate_gtm_brief(_make_idea())
        assert len(brief["first_ten_customers"]) >= 3
        assert len(brief["channels"]) >= 2
        assert len(brief["launch_checklist"]) >= 4

    def test_unparseable_llm_response_falls_back(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        bad = MagicMock()
        bad.name = "stub"
        bad.call = MagicMock(return_value="this is not json at all")

        brief = generate_gtm_brief(_make_idea(), backend=bad)
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))

    def test_missing_key_in_llm_response_falls_back(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        incomplete = dict(_FULL_PAYLOAD)
        del incomplete["launch_checklist"]

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(incomplete))
        # Should fall back to heuristic which always has the key.
        assert isinstance(brief["launch_checklist"], list)
        assert len(brief["launch_checklist"]) > 0

    def test_list_field_not_a_list_falls_back(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        bad_type = dict(_FULL_PAYLOAD)
        bad_type["channels"] = "just a string"

        brief = generate_gtm_brief(_make_idea(), backend=_stub_backend(bad_type))
        assert isinstance(brief["channels"], list)

    def test_backend_call_exception_falls_back(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        exploding = MagicMock()
        exploding.name = "stub"
        exploding.call = MagicMock(side_effect=RuntimeError("connection reset"))

        brief = generate_gtm_brief(_make_idea(), backend=exploding)
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))

    def test_backend_returns_none_falls_back(self):
        from project_forge.engine.launchpad import generate_gtm_brief

        none_returning = MagicMock()
        none_returning.name = "stub"
        none_returning.call = MagicMock(return_value=None)

        brief = generate_gtm_brief(_make_idea(), backend=none_returning)
        assert _REQUIRED_KEYS.issubset(set(brief.keys()))


# --------------------------------------------------------------------------- #
# Category-specific heuristic content                                         #
# --------------------------------------------------------------------------- #


class TestHeuristicCategoryMapping:
    def test_security_tool_icp_mentions_security(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        idea = _make_idea(category=IdeaCategory.SECURITY_TOOL)
        brief = lp.generate_gtm_brief(idea)
        assert "security" in brief["icp"].lower() or "ciso" in brief["icp"].lower()

    def test_micro_saas_pricing_mentions_freemium(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        idea = _make_idea(category=IdeaCategory.MICRO_SAAS)
        brief = lp.generate_gtm_brief(idea)
        assert "freemium" in brief["pricing"].lower() or "$" in brief["pricing"]

    def test_unknown_category_gets_default_channels(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        # NIST_STANDARDS has no channel mapping — should fall back to defaults.
        idea = _make_idea(category=IdeaCategory.NIST_STANDARDS)
        brief = lp.generate_gtm_brief(idea)
        assert isinstance(brief["channels"], list)
        assert len(brief["channels"]) > 0

    def test_landing_headline_uses_tagline(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        idea = _make_idea(tagline="stop losing money on rate limits")
        brief = lp.generate_gtm_brief(idea)
        assert "rate limits" in brief["landing_headline"].lower()

    def test_cold_open_mentions_idea_name(self, monkeypatch):
        import project_forge.engine.launchpad as lp

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        brief = lp.generate_gtm_brief(_make_idea())
        assert "RateGuard" in brief["cold_open"]


# --------------------------------------------------------------------------- #
# format_brief_markdown                                                       #
# --------------------------------------------------------------------------- #


class TestFormatBriefMarkdown:
    def test_renders_all_section_headings(self):
        from project_forge.engine.launchpad import format_brief_markdown

        md = format_brief_markdown(_FULL_PAYLOAD)
        for section in (
            "## Positioning",
            "## Ideal Customer Profile",
            "## First 10 Customers",
            "## Launch Channels",
            "## Pricing",
            "## Landing Page Headline",
            "## Cold Open Message",
            "## Launch Checklist",
        ):
            assert section in md, f"missing section: {section!r}"

    def test_list_items_are_bullet_points(self):
        from project_forge.engine.launchpad import format_brief_markdown

        md = format_brief_markdown(_FULL_PAYLOAD)
        # Each item in first_ten_customers should appear as "- <item>"
        for item in _FULL_PAYLOAD["first_ten_customers"]:
            assert f"- {item}" in md

    def test_backend_line_appears_when_present(self):
        from project_forge.engine.launchpad import format_brief_markdown

        brief = dict(_FULL_PAYLOAD, _backend="claude-code:opus")
        md = format_brief_markdown(brief)
        assert "claude-code:opus" in md

    def test_no_backend_line_when_absent(self):
        from project_forge.engine.launchpad import format_brief_markdown

        md = format_brief_markdown(_FULL_PAYLOAD)
        assert "Generated by" not in md

    def test_renders_heuristic_brief(self, monkeypatch):
        import project_forge.engine.launchpad as lp
        from project_forge.engine.launchpad import format_brief_markdown

        monkeypatch.setattr(lp, "resolve_cheap_backend", lambda: None)
        brief = lp.generate_gtm_brief(_make_idea())
        md = format_brief_markdown(brief)
        assert "## Positioning" in md
        assert "## Launch Checklist" in md
        assert len(md) > 200

    def test_output_is_string(self):
        from project_forge.engine.launchpad import format_brief_markdown

        result = format_brief_markdown(_FULL_PAYLOAD)
        assert isinstance(result, str)
