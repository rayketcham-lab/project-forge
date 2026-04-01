"""Prompt templates for divergent thinking idea generation."""

import random

from project_forge.engine.categories import (
    CATEGORY_SEEDS,
    COMBINATORIC_TEMPLATES,
    CONTRARIAN_PROMPTS,
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

IMPORTANT CONSTRAINTS:
- The idea must be DIFFERENT from these recently generated ideas: {recent_ideas}
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


def build_generation_prompt(
    category: IdeaCategory,
    recent_ideas: list[str],
    use_contrarian: bool = False,
    use_combinatoric: bool = False,
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

    recent_str = ", ".join(recent_ideas[-5:]) if recent_ideas else "None yet"

    return GENERATION_PROMPT_TEMPLATE.format(
        category=category.value,
        category_description=seeds["description"],
        diversity_section=diversity_section,
        recent_ideas=recent_str,
        category_value=category.value,
    )
