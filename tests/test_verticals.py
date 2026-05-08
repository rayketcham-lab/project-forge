"""TDD: vertical (industry) inference for cross-cutting filtering.

Today the explore UI has a single tech-axis filter (security-tool, observability,
etc.). This module adds an inferred VERTICAL axis (government, healthcare,
education, finance, retail, hospitality, manufacturing, energy, telco) so the
user can drill down into a slice they care about, mute one they don't, or
browse for serendipity.

Inference is keyword-based against name + tagline + description. An idea may
have zero, one, or several verticals. No verticals = "general" (shown by default
under any vertical filter so users don't miss horizontals).
"""

from __future__ import annotations

import pytest

from project_forge.engine.verticals import (
    KNOWN_VERTICALS,
    infer_verticals,
    matches_vertical,
)
from project_forge.models import Idea, IdeaCategory


def _idea(name: str = "X", tagline: str = "t", description: str = "d") -> Idea:
    return Idea(
        name=name,
        tagline=tagline,
        description=description,
        category=IdeaCategory.SECURITY_TOOL,
        market_analysis="m",
        feasibility_score=0.8,
        mvp_scope="mvp",
        tech_stack=["python"],
    )


class TestInferVerticals:
    def test_government_keywords(self):
        v = infer_verticals(_idea(
            description="A FedRAMP compliance tool for federal agencies migrating to FIPS 140-3.",
        ))
        assert "government" in v

    def test_healthcare_keywords(self):
        v = infer_verticals(_idea(
            description="HIPAA-compliant patient EHR audit tool for hospitals.",
        ))
        assert "healthcare" in v

    def test_education_keywords(self):
        v = infer_verticals(_idea(
            description="A FERPA-aware student records anonymization tool for K-12 schools.",
        ))
        assert "education" in v

    def test_hospitality_keywords(self):
        v = infer_verticals(_idea(
            description="Hotel guest WiFi captive portal with stay-duration token rotation.",
        ))
        assert "hospitality" in v

    def test_finance_keywords(self):
        v = infer_verticals(_idea(
            description="PCI-DSS audit automation for fintech payment processors.",
        ))
        assert "finance" in v

    def test_no_match_returns_empty_list(self):
        v = infer_verticals(_idea(
            description="A general-purpose CLI to grep YAML files.",
        ))
        assert v == []

    def test_multi_vertical(self):
        v = infer_verticals(_idea(
            description="HIPAA + FedRAMP combined compliance dashboard for federal hospitals.",
        ))
        assert "government" in v
        assert "healthcare" in v

    def test_inference_uses_name_and_tagline_too(self):
        v = infer_verticals(_idea(
            name="University SSO Bridge",
            tagline="A single sign-on bridge for campus identity systems",
            description="Generic.",
        ))
        assert "education" in v

    def test_case_insensitive(self):
        v = infer_verticals(_idea(description="HOSPITAL EHR thing"))
        assert "healthcare" in v

    def test_word_boundary_avoids_false_positives(self):
        """'banking' should not match 'bank' as a substring of unrelated word."""
        v = infer_verticals(_idea(description="rambank cache eviction."))
        # 'rambank' is not a banking idea
        assert "finance" not in v


class TestKnownVerticals:
    def test_canonical_set_present(self):
        # Stable set — UI relies on these slugs
        for slug in ("government", "healthcare", "education", "finance",
                     "retail", "hospitality", "manufacturing", "energy", "telco"):
            assert slug in KNOWN_VERTICALS, f"Missing canonical vertical: {slug}"


class TestMatchesVertical:
    def test_idea_matches_when_inferred(self):
        idea = _idea(description="HIPAA EHR integration")
        assert matches_vertical(idea, "healthcare") is True

    def test_idea_does_not_match_unrelated_vertical(self):
        idea = _idea(description="Generic developer tool")
        assert matches_vertical(idea, "healthcare") is False

    def test_unknown_vertical_returns_false(self):
        idea = _idea(description="anything")
        assert matches_vertical(idea, "not-a-real-vertical") is False

    @pytest.mark.parametrize("vertical,desc,expected", [
        ("government", "DoD ATO automation tool", True),
        ("government", "tool for hotels", False),
        ("hospitality", "tool for hotels", True),
        ("manufacturing", "OT/ICS network monitor for factory floors", True),
        ("energy", "smart meter tampering detector", True),
        ("telco", "5G core slicing observer", True),
    ])
    def test_parametrized(self, vertical: str, desc: str, expected: bool):
        idea = _idea(description=desc)
        assert matches_vertical(idea, vertical) is expected
