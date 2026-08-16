"""Prompt templates for divergent thinking idea generation."""

import random

from project_forge.engine.categories import (
    CATEGORY_SEEDS,
    COMBINATORIC_TEMPLATES,
    CONTRARIAN_PROMPTS,
    PERSONA_SEEDS,
)
from project_forge.models import IdeaCategory

SYSTEM_PROMPT = """You are an IT project think-tank engine. You think like a showrunner who sees \
technology from unexpected angles -- like Black Mirror meets Fargo. Your ideas are grounded in \
real technical feasibility but approach problems from directions nobody else considers.

You generate project ideas that are:
- NOVEL: Not another todo app, not another dashboard. Something that makes people say "why doesn't this exist?"
- TANGIBLE: Can be built as an MVP in 2-4 weeks by a small team
- VALUABLE: Solves a real pain point that real engineers/companies face
- SPECIFIC: Not vague concepts but concrete tools with clear scope

You output structured JSON. Every idea must be buildable, not theoretical."""

GENERATION_PROMPT_TEMPLATE = """Generate ONE novel IT project idea in the category: {category}
Category description: {category_description}

{diversity_section}

{portfolio_section}{saturation_section}{external_signals_section}IMPORTANT CONSTRAINTS:
- The idea must be DIFFERENT from these recently generated ideas (name: tagline pairs — \
avoid reusing their phrasing/hook, not just their name): {recent_ideas}
- Think about what's MISSING in the market, not what already exists
- Consider the intersection of this category with unexpected domains
- The MVP must be achievable in 2-4 weeks

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Project Name (2-4 words)",
    "tagline": "One-sentence hook (under 100 chars)",
    "description": "2-3 paragraph pitch explaining the problem, the solution, and why now",
    "category": "{category_value}",
    "market_analysis": "2-3 sentences on why this matters now, what's the gap, who needs it",
    "feasibility_score": 0.75,
    "mvp_scope": "Concrete description of what the MVP includes and doesn't include",
    "tech_stack": ["python", "fastapi", "sqlite"]
}}

The feasibility_score should be between 0.0 and 1.0 where:
- 0.0-0.3: Interesting but very hard to build or unclear market
- 0.3-0.5: Feasible but significant unknowns
- 0.5-0.7: Solid idea, clear path to MVP
- 0.7-0.9: Strong idea, achievable MVP, clear market need
- 0.9-1.0: Obviously needed, straightforward to build, immediate value"""


URL_INGEST_PROMPT_TEMPLATE = """Analyze the following content from a URL and generate ONE novel IT project idea \
inspired by the technologies, problems, or opportunities described.

**Source:** {title}
**URL:** {url}
**Domain:** {domain}

**Content excerpt:**
{content}

{category_section}

Based on this content, identify:
1. What technology or trend is being described
2. What gap or opportunity exists for a new tool/project
3. How a small team could build something valuable in this space

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Project Name (2-4 words)",
    "tagline": "One-sentence hook (under 100 chars)",
    "description": "2-3 paragraph pitch explaining the problem, the solution, and why now",
    "category": "{category_value}",
    "market_analysis": "2-3 sentences on why this matters now, what's the gap, who needs it",
    "feasibility_score": 0.75,
    "mvp_scope": "Concrete description of what the MVP includes and doesn't include",
    "tech_stack": ["python", "fastapi", "sqlite"]
}}

The feasibility_score should be between 0.0 and 1.0 where:
- 0.0-0.3: Interesting but very hard to build or unclear market
- 0.3-0.5: Feasible but significant unknowns
- 0.5-0.7: Solid idea, clear path to MVP
- 0.7-0.9: Strong idea, achievable MVP, clear market need
- 0.9-1.0: Obviously needed, straightforward to build, immediate value"""


TEXT_INGEST_PROMPT_TEMPLATE = """You are an idea-builder. The user has shared \
a raw fragment of thought — a partial sentence, a research question, a \
frustration, a code snippet, or a domain observation. Your job is to expand \
it into a fully-developed, buildable IT project idea.

**User fragment:**
---
{text}
---

{category_section}

Identify:
1. The core problem or opportunity hidden in the fragment
2. Who would use the resulting tool (concrete persona, not abstract)
3. A concrete project that addresses it
4. The MVP scope achievable in 2-4 weeks by a small team
5. Why now — what changed that makes this worth building today

Keep the fragment's intent. If the user wrote "I keep having to reconcile \
SBOMs across forks", don't drift into a general supply-chain dashboard — \
build the specific reconciliation tool they hinted at.

Respond with ONLY valid JSON in this exact format:
{{
    "name": "Short Project Name (2-4 words)",
    "tagline": "One-sentence hook (under 100 chars)",
    "description": "2-3 paragraph pitch explaining the problem, the solution, and why now",
    "category": "{category_value}",
    "market_analysis": "2-3 sentences on why this matters now, what's the gap, who needs it",
    "feasibility_score": 0.75,
    "mvp_scope": "Concrete description of what the MVP includes and doesn't include",
    "tech_stack": ["python", "fastapi", "sqlite"]
}}

The feasibility_score should be between 0.0 and 1.0:
- 0.0-0.3: Interesting but very hard to build or unclear market
- 0.3-0.5: Feasible but significant unknowns
- 0.5-0.7: Solid idea, clear path to MVP
- 0.7-0.9: Strong idea, achievable MVP, clear market need
- 0.9-1.0: Obviously needed, straightforward to build, immediate value"""


def build_text_ingest_prompt(text: str, category_hint: str | None = None) -> str:
    """Build a prompt for expanding a free-form text fragment into an Idea."""
    if category_hint:
        category_section = f"SUGGESTED CATEGORY: {category_hint} (use this if it fits the fragment)"
        category_value = category_hint
    else:
        all_cats = ", ".join(c.value for c in IdeaCategory)
        category_section = f"Choose the most fitting category from: {all_cats}"
        category_value = "security-tool"

    return TEXT_INGEST_PROMPT_TEMPLATE.format(
        text=text,
        category_section=category_section,
        category_value=category_value,
    )


def build_url_ingest_prompt(
    title: str,
    url: str,
    domain: str,
    content: str,
    category_hint: str | None = None,
) -> str:
    """Build a prompt for generating an idea from URL content."""
    if category_hint:
        category_section = f"SUGGESTED CATEGORY: {category_hint} (use this if the content fits)"
        category_value = category_hint
    else:
        all_cats = ", ".join(c.value for c in IdeaCategory)
        category_section = f"Choose the most fitting category from: {all_cats}"
        category_value = "security-tool"  # default placeholder, Claude will pick

    return URL_INGEST_PROMPT_TEMPLATE.format(
        title=title,
        url=url,
        domain=domain,
        content=content[:3000],
        category_section=category_section,
        category_value=category_value,
    )


def _format_external_signals_section(external_seeds: list[dict] | None) -> str:
    """Format the external-signals block. Empty string when no seeds."""
    if not external_seeds:
        return ""

    from project_forge.feeds import format_for_prompt

    rendered = format_for_prompt(external_seeds, max_items=5)
    if not rendered:
        return ""
    return (
        "EXTERNAL SIGNALS — recent items from CVE feeds, arXiv, IETF drafts. "
        "These are FRESH starting points; the gap they point to is real and current.\n"
        f"{rendered}\n\n"
    )


def _format_saturation_section(filter_summary: dict | None) -> str:
    """Format the saturation block. Empty string when no useful data."""
    if not filter_summary:
        return ""

    saturated = filter_summary.get("saturated_concepts") or []
    high_rate = filter_summary.get("high_filter_rate_categories") or []
    if not saturated and not high_rate:
        return ""

    lines = ["SATURATION SIGNAL — derived from rejected ideas (avoid these)."]
    if saturated:
        lines.append(
            f"Saturated concepts (do NOT center your idea on these): {', '.join(saturated)}",
        )
        lines.append(
            "These exact words are also banned from your tagline and name outright — "
            "not just as the idea's theme. If a saturated word is the only way to "
            "describe your idea, that is a signal to pick a different idea.",
        )
    if high_rate:
        rate_strs = [f"{cat} ({rate:.0%})" for cat, rate in high_rate]
        lines.append(f"High filter-rate categories: {', '.join(rate_strs)}")
    return "\n".join(lines) + "\n\n"


def build_generation_prompt(
    category: IdeaCategory,
    recent_ideas: list[str],
    use_contrarian: bool = False,
    use_combinatoric: bool = False,
    portfolio_context: str | None = None,
    *,
    filter_summary: dict | None = None,
    external_seeds: list[dict] | None = None,
) -> str:
    seeds = CATEGORY_SEEDS[category]
    diversity_section = ""

    if use_contrarian:
        prompt = random.choice(CONTRARIAN_PROMPTS)
        diversity_section = f"CREATIVE DIRECTION: {prompt}\n"

    if use_combinatoric:
        template = random.choice(COMBINATORIC_TEMPLATES)
        concept_a = random.choice(seeds["seed_concepts"])
        concept_b = random.choice(seeds["seed_concepts"])
        domain_a = category.value
        domain_b = random.choice(seeds["domains_to_cross"])
        filled = template.format(
            concept_a=concept_a,
            concept_b=concept_b,
            domain_a=domain_a,
            domain_b=domain_b,
        )
        diversity_section += f"CROSS-POLLINATION SEED: {filled}\n"

    if not use_contrarian and not use_combinatoric:
        seed = random.choice(seeds["seed_concepts"])
        domain = random.choice(seeds["domains_to_cross"])
        diversity_section = f"SEED CONCEPT: Consider the space around '{seed}' applied to '{domain}'\n"

    # Always inject a persona — grounds the idea in a specific human's problem
    persona = random.choice(PERSONA_SEEDS)
    diversity_section += f"\nPERSPECTIVE: Design this for a {persona['role']}. Their situation: {persona['pain']}\n"

    recent_str = "; ".join(recent_ideas[-10:]) if recent_ideas else "None yet"

    if portfolio_context:
        portfolio_section = (
            f"EXISTING PORTFOLIO — do NOT generate ideas that belong to these repos:\n{portfolio_context}\n"
        )
    else:
        portfolio_section = ""

    return GENERATION_PROMPT_TEMPLATE.format(
        category=category.value,
        category_description=seeds["description"],
        diversity_section=diversity_section,
        portfolio_section=portfolio_section,
        saturation_section=_format_saturation_section(filter_summary),
        external_signals_section=_format_external_signals_section(external_seeds),
        recent_ideas=recent_str,
        category_value=category.value,
    )
