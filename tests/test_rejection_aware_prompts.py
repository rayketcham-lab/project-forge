"""TDD: Direction A — rejection-aware generation prompts.

Phase 4 (issue #57). build_generation_prompt gains an optional
filter_summary parameter. When present, a saturation summary is injected
into the prompt so Claude knows which concepts to avoid.

Default off → prompt remains backward-compatible.
"""

from __future__ import annotations

from project_forge.engine.prompts import build_generation_prompt
from project_forge.models import IdeaCategory


def _baseline_kwargs():
    return {
        "category": IdeaCategory.SECURITY_TOOL,
        "recent_ideas": [],
    }


class TestFilterSummaryInjection:
    def test_default_omits_filter_summary(self):
        """No filter_summary kwarg → no saturation section in prompt."""
        prompt = build_generation_prompt(**_baseline_kwargs())
        assert "Saturated concepts" not in prompt
        assert "filter_rate" not in prompt.lower()

    def test_filter_summary_none_omits_section(self):
        """Explicit filter_summary=None → no saturation section."""
        prompt = build_generation_prompt(**_baseline_kwargs(), filter_summary=None)
        assert "Saturated concepts" not in prompt

    def test_filter_summary_with_data_includes_saturated_concepts(self):
        summary = {
            "saturated_concepts": ["certificate", "detection", "compliance"],
            "high_filter_rate_categories": [
                ("nist-standards", 0.80),
                ("security-tool", 0.73),
            ],
        }
        prompt = build_generation_prompt(**_baseline_kwargs(), filter_summary=summary)
        assert "certificate" in prompt
        assert "detection" in prompt
        assert "compliance" in prompt

    def test_filter_summary_includes_filter_rates(self):
        summary = {
            "saturated_concepts": [],
            "high_filter_rate_categories": [
                ("nist-standards", 0.80),
            ],
        }
        prompt = build_generation_prompt(**_baseline_kwargs(), filter_summary=summary)
        assert "nist-standards" in prompt
        assert "80" in prompt  # rate appears as percentage or decimal

    def test_filter_summary_empty_dict_no_section(self):
        """Empty filter_summary → no saturation noise added to prompt."""
        prompt = build_generation_prompt(
            **_baseline_kwargs(),
            filter_summary={"saturated_concepts": [], "high_filter_rate_categories": []},
        )
        # No "Saturated concepts" header when both lists empty
        assert "Saturated concepts" not in prompt
        assert "High filter-rate" not in prompt

    def test_avoid_instruction_present(self):
        """Prompt instructs LLM to avoid saturated concepts."""
        summary = {
            "saturated_concepts": ["certificate"],
            "high_filter_rate_categories": [],
        }
        prompt = build_generation_prompt(**_baseline_kwargs(), filter_summary=summary)
        # Some instruction telling Claude to avoid these
        lower = prompt.lower()
        assert "avoid" in lower or "saturated" in lower
