"""Tests for prompt template generation."""

from project_forge.engine.prompts import SYSTEM_PROMPT, build_generation_prompt
from project_forge.models import IdeaCategory


def test_system_prompt_exists():
    assert len(SYSTEM_PROMPT) > 100
    assert "think-tank" in SYSTEM_PROMPT


def test_build_basic_prompt():
    prompt = build_generation_prompt(
        category=IdeaCategory.SECURITY_TOOL,
        recent_ideas=[],
    )
    assert "security-tool" in prompt
    assert "JSON" in prompt
    assert "feasibility_score" in prompt


def test_build_prompt_with_recent_ideas():
    prompt = build_generation_prompt(
        category=IdeaCategory.AUTOMATION,
        recent_ideas=["Idea A", "Idea B", "Idea C"],
    )
    assert "Idea A" in prompt
    assert "Idea B" in prompt


def test_build_prompt_keeps_last_ten_recent_ideas():
    recent = [f"Idea {i}" for i in range(15)]
    prompt = build_generation_prompt(
        category=IdeaCategory.AUTOMATION,
        recent_ideas=recent,
    )
    assert "Idea 5" in prompt
    assert "Idea 14" in prompt
    assert "Idea 0" not in prompt


def test_build_prompt_with_tagline_pairs_surfaces_taglines():
    prompt = build_generation_prompt(
        category=IdeaCategory.AUTOMATION,
        recent_ideas=["Cert Mapper: auto-inventories expiring TLS certs across clouds"],
    )
    assert "auto-inventories expiring TLS certs across clouds" in prompt


def test_build_contrarian_prompt():
    prompt = build_generation_prompt(
        category=IdeaCategory.MARKET_GAP,
        recent_ideas=[],
        use_contrarian=True,
    )
    assert "CREATIVE DIRECTION" in prompt


def test_build_combinatoric_prompt():
    prompt = build_generation_prompt(
        category=IdeaCategory.DEVOPS_TOOLING,
        recent_ideas=[],
        use_combinatoric=True,
    )
    assert "CROSS-POLLINATION SEED" in prompt


def test_build_prompt_with_both_modes():
    prompt = build_generation_prompt(
        category=IdeaCategory.PRIVACY,
        recent_ideas=[],
        use_contrarian=True,
        use_combinatoric=True,
    )
    assert "CREATIVE DIRECTION" in prompt
    assert "CROSS-POLLINATION SEED" in prompt


def test_all_categories_have_prompts():
    for category in IdeaCategory:
        prompt = build_generation_prompt(category=category, recent_ideas=[])
        assert category.value in prompt
        assert len(prompt) > 200
