"""Launchpad — autonomous go-to-market brief generator.

Turns a fundable idea into a launch-ready GTM brief covering positioning,
ICP, first-ten-customer playbook, channels, pricing, landing copy, and a
launch checklist.

Two-stage, matching the snipe/ambition pattern:

  1. Heuristic fallback (always available, ~free):
     Derives a usable brief from idea.category, tech_stack, and market_analysis.
     Not as crisp as the LLM version, but always returns a structurally valid dict.

  2. LLM brief (when a backend resolves):
     Asks the cheap backend for a JSON blob. Codefence-stripped, validated.
     Falls back gracefully to the heuristic if the LLM response is unparseable.

Public API:
    generate_gtm_brief(idea, *, backend=None) -> dict
    format_brief_markdown(brief) -> str
"""

from __future__ import annotations

import json
import logging
from typing import Any

from project_forge.engine.llm_backend import resolve_cheap_backend
from project_forge.models import Idea, IdeaCategory

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# ICP and channel defaults by category                                        #
# --------------------------------------------------------------------------- #

_CATEGORY_ICP: dict[IdeaCategory, str] = {
    IdeaCategory.SECURITY_TOOL: "Security engineer or CISO at a mid-market company (100–500 employees)",
    IdeaCategory.DEVOPS_TOOLING: "Platform engineer or DevOps lead managing cloud-native infrastructure",
    IdeaCategory.OBSERVABILITY: "SRE or DevOps engineer on-call for a production service",
    IdeaCategory.COMPLIANCE: "Compliance officer or GRC analyst at a regulated enterprise",
    IdeaCategory.AUTOMATION: "Operations manager looking to eliminate repetitive manual work",
    IdeaCategory.AUTOMATION_INCOME: "Indie hacker or solo founder building online income streams",
    IdeaCategory.CONSUMER_APP: "Early-adopter consumer aged 25–40, tech-comfortable but not technical",
    IdeaCategory.PRODUCTIVITY: "Knowledge worker drowning in tabs, context-switching, and meetings",
    IdeaCategory.CREATOR_TOOLS: "Content creator or small media brand monetizing an audience",
    IdeaCategory.MICRO_SAAS: "Solo founder or small team building a niche B2B workflow tool",
    IdeaCategory.VERTICAL_SAAS: (
        "Operator in a specific vertical (healthcare, legal, construction) underserved by horizontal tools"
    ),
    IdeaCategory.ECOMMERCE_TOOLS: "DTC ecommerce operator running Shopify at $1M–$10M annual GMV",
    IdeaCategory.FINTECH_TOOLS: "Finance or ops lead at a startup needing financial tooling without enterprise pricing",
    IdeaCategory.CLAUDE_SKILLS_AGENTS: "Developer building Claude-powered workflows or AI agents",
    IdeaCategory.AI_MARKETPLACE: "AI developer or prompt engineer looking to publish or acquire agent skills",
    IdeaCategory.AGENT_INFRA: "AI platform engineer running multi-agent systems in production",
    IdeaCategory.AGENT_SECURITY: "Security team responsible for AI/LLM deployments",
    IdeaCategory.CONTEXT_MEMORY: "AI developer who needs persistent, structured context across agent sessions",
    IdeaCategory.CLAUDE_EVALS: "ML engineer or AI product lead shipping LLM-powered features",
    IdeaCategory.PQC_CRYPTOGRAPHY: "Cryptography engineer or CISO preparing for post-quantum migration",
    IdeaCategory.PRIVACY: "Privacy engineer or DPO at a company subject to GDPR or CCPA",
    IdeaCategory.CRYPTO_INFRASTRUCTURE: (
        "PKI or certificate operations engineer managing large-scale TLS infrastructure"
    ),
    IdeaCategory.MARKET_GAP: "Technically-sophisticated buyer who has already tried and rejected incumbent solutions",
    IdeaCategory.SELF_IMPROVEMENT: "Engineering team lead investing in developer productivity and code quality",
}

_CATEGORY_CHANNELS: dict[IdeaCategory, list[str]] = {
    IdeaCategory.SECURITY_TOOL: [
        "Security Twitter/X",
        "BSides / DEF CON talks",
        "CISO Slack communities",
        "LinkedIn direct outreach",
    ],
    IdeaCategory.DEVOPS_TOOLING: [
        "Hacker News Show HN",
        "Reddit r/devops",
        "CNCF Slack",
        "DevOps Days sponsorship",
    ],
    IdeaCategory.AUTOMATION: [
        "ProductHunt launch",
        "Reddit r/automation",
        "LinkedIn content marketing",
    ],
    IdeaCategory.AUTOMATION_INCOME: [
        "Indie Hackers",
        "Twitter/X build-in-public",
        "ProductHunt",
    ],
    IdeaCategory.CONSUMER_APP: [
        "TikTok demo",
        "ProductHunt",
        "App Store optimization (ASO)",
        "influencer seeding",
    ],
    IdeaCategory.MICRO_SAAS: [
        "Indie Hackers",
        "Twitter/X build-in-public",
        "cold email to niche list",
    ],
    IdeaCategory.VERTICAL_SAAS: [
        "industry trade publications",
        "niche LinkedIn groups",
        "conference sponsorship",
        "partnership with vertical-specific resellers",
    ],
    IdeaCategory.CREATOR_TOOLS: [
        "YouTube demo",
        "Twitter/X creator community",
        "ProductHunt",
        "newsletter sponsorship",
    ],
    IdeaCategory.CLAUDE_SKILLS_AGENTS: [
        "Anthropic developer Discord",
        "Hacker News",
        "GitHub trending",
        "AI Twitter/X",
    ],
    IdeaCategory.AI_MARKETPLACE: [
        "AI Twitter/X",
        "ProductHunt",
        "GitHub",
        "developer newsletters (TLDR AI, The Batch)",
    ],
}

_DEFAULT_CHANNELS = ["Hacker News Show HN", "ProductHunt", "Twitter/X build-in-public", "targeted cold email"]

_CATEGORY_PRICING: dict[IdeaCategory, str] = {
    IdeaCategory.MICRO_SAAS: "Freemium — free tier (usage-capped), $29/mo solo, $79/mo team",
    IdeaCategory.VERTICAL_SAAS: "$199–$499/mo per seat; annual discount 20%; custom enterprise on request",
    IdeaCategory.SECURITY_TOOL: "$0 open-source core; $149/mo hosted / support tier",
    IdeaCategory.DEVOPS_TOOLING: "$0 open-source; $49/mo hosted SaaS; $999/mo self-hosted enterprise",
    IdeaCategory.CONSUMER_APP: "$0 free tier; $9/mo Pro; $19/mo Pro+",
    IdeaCategory.AUTOMATION_INCOME: "$49 one-time or $9/mo subscription; free 14-day trial",
    IdeaCategory.CLAUDE_SKILLS_AGENTS: "$0 open-source or $19/mo hosted; marketplace rev-share on paid extensions",
    IdeaCategory.AI_MARKETPLACE: "Freemium platform; 20% rev-share on paid skill sales; $29/mo Pro creator",
}

_DEFAULT_PRICING = "Freemium — free tier to remove friction, paid tier at $29–$79/mo once value is proven"


def _heuristic_first_ten(idea: Idea) -> list[str]:
    """Derive first-ten-customer playbook from category and idea text."""
    icp = _CATEGORY_ICP.get(idea.category, "target buyer")
    return [
        f"Post a 'Show HN: {idea.name}' — {idea.tagline}. Respond to every comment.",
        f"DM 10 {icp}s who've complained publicly about the problem. Offer free access.",
        "Ask each early user to refer one peer in exchange for lifetime discount.",
        "Write a short case study for each of the first five users — post to your blog and LinkedIn.",
        "Launch on ProductHunt on a Tuesday; prep a gallery of 3 screenshots and a 60-second demo GIF.",
    ]


def _heuristic_checklist(idea: Idea) -> list[str]:
    stack_note = f"Tech: {', '.join(idea.tech_stack[:3])}" if idea.tech_stack else "Pick minimal stack"
    return [
        "Set up a one-page landing with headline, 3-bullet value prop, and email capture",
        f"Build the thinnest possible demo: {idea.mvp_scope[:80] if idea.mvp_scope else 'core feature only'}",
        stack_note,
        "Record a <90-second Loom/screen demo — post it everywhere before writing a line of sales copy",
        "Get 5 non-friend strangers to look at the landing and give brutal feedback",
        "Set up a free Stripe account and put pricing live on day 1 — even before the product ships",
        "Track one north-star metric: weekly active users (or conversions if paid)",
        "Schedule a weekly 15-minute retrospective with any co-founder / accountability partner",
    ]


def _generate_heuristic_brief(idea: Idea) -> dict[str, Any]:
    """Deterministic GTM brief derived from the Idea fields. Always usable."""
    icp = _CATEGORY_ICP.get(idea.category, "technically-sophisticated early adopter")
    channels = _CATEGORY_CHANNELS.get(idea.category, _DEFAULT_CHANNELS)
    pricing = _CATEGORY_PRICING.get(idea.category, _DEFAULT_PRICING)

    market_ctx = idea.market_analysis[:120] if idea.market_analysis else "need a better solution"
    mvp_hint = idea.mvp_scope[:80] if idea.mvp_scope else "a focused MVP"
    positioning = (
        f"For {icp}s who {market_ctx}, {idea.name} is {idea.tagline}. Unlike existing tools, it ships {mvp_hint} first."
    )

    cold_open = (
        f"Hi — I'm building {idea.name}: {idea.tagline}. "
        f"I noticed you might care about this because of the problem in {idea.category.value.replace('-', ' ')}. "
        "Would a 10-minute call to share an early demo be worth your time?"
    )

    return {
        "positioning": positioning,
        "icp": icp,
        "first_ten_customers": _heuristic_first_ten(idea),
        "channels": channels,
        "pricing": pricing,
        "landing_headline": idea.tagline or idea.name,
        "cold_open": cold_open,
        "launch_checklist": _heuristic_checklist(idea),
    }


# --------------------------------------------------------------------------- #
# LLM-enhanced brief                                                          #
# --------------------------------------------------------------------------- #

_BRIEF_KEYS = frozenset(
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


def _build_prompt(idea: Idea) -> str:
    return (
        "You are a go-to-market strategist. Produce a launch brief for the following "
        "project idea. Respond with JSON only — no prose, no markdown outside the JSON.\n\n"
        f"## Idea: {idea.name}\n"
        f"**Tagline:** {idea.tagline}\n"
        f"**Category:** {idea.category.value}\n"
        f"**Description:** {idea.description}\n"
        f"**Market:** {idea.market_analysis}\n"
        f"**MVP scope:** {idea.mvp_scope}\n"
        f"**Tech stack:** {', '.join(idea.tech_stack)}\n"
        f"**Feasibility:** {idea.feasibility_score:.2f}\n\n"
        "Return exactly this JSON shape:\n"
        "{\n"
        '  "positioning": "one-sentence positioning statement",\n'
        '  "icp": "ideal customer profile — one sentence",\n'
        '  "first_ten_customers": ["step 1", "step 2", "step 3", "step 4", "step 5"],\n'
        '  "channels": ["channel 1", "channel 2", "channel 3"],\n'
        '  "pricing": "pricing model — one or two sentences",\n'
        '  "landing_headline": "hero headline for the landing page",\n'
        '  "cold_open": "cold outreach message to send to the first prospect",\n'
        '  "launch_checklist": ["item 1", "item 2", "item 3", "item 4", "item 5"]\n'
        "}"
    )


def _strip_codefence(raw: str) -> str:
    """Strip ```json … ``` or ``` … ``` wrappers if present."""
    if "```json" in raw:
        raw = raw.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in raw:
        raw = raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _parse_and_validate(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Parse LLM JSON; return fallback on any parse/validation failure."""
    try:
        data: dict[str, Any] = json.loads(_strip_codefence(raw))
    except Exception:
        logger.info("launchpad LLM response not valid JSON; using heuristic brief")
        return fallback

    missing = _BRIEF_KEYS - set(data.keys())
    if missing:
        logger.info("launchpad LLM response missing keys %s; using heuristic brief", missing)
        return fallback

    # Ensure list fields are actually lists.
    for list_key in ("first_ten_customers", "channels", "launch_checklist"):
        if not isinstance(data.get(list_key), list):
            logger.info("launchpad LLM key %r not a list; using heuristic brief", list_key)
            return fallback

    return data


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


def generate_gtm_brief(idea: Idea, *, backend: Any = None) -> dict[str, Any]:
    """Generate a go-to-market brief for *idea*.

    Uses *backend* if provided, otherwise resolves the cheap backend.
    Falls back to a deterministic heuristic brief when no backend is available
    or the LLM response is unparseable.

    Returns a dict with keys:
        positioning, icp, first_ten_customers (list), channels (list),
        pricing, landing_headline, cold_open, launch_checklist (list).
    """
    fallback = _generate_heuristic_brief(idea)

    resolved_backend = backend if backend is not None else resolve_cheap_backend()
    if resolved_backend is None:
        logger.debug("launchpad: no LLM backend available; returning heuristic brief")
        return fallback

    prompt = _build_prompt(idea)
    try:
        raw = resolved_backend.call(prompt) or ""
    except Exception as exc:
        logger.warning("launchpad backend call raised: %s", exc)
        return fallback

    brief = _parse_and_validate(raw, fallback)
    brief["_backend"] = resolved_backend.name
    return brief


def format_brief_markdown(brief: dict[str, Any]) -> str:
    """Render a GTM brief dict as human-readable Markdown.

    Safe to call with the heuristic fallback dict or the LLM dict — both
    carry the same keys.
    """
    lines: list[str] = []

    def _section(title: str, content: str | list[str]) -> None:
        lines.append(f"## {title}")
        if isinstance(content, list):
            for item in content:
                lines.append(f"- {item}")
        else:
            lines.append(str(content))
        lines.append("")

    _section("Positioning", brief.get("positioning", ""))
    _section("Ideal Customer Profile (ICP)", brief.get("icp", ""))
    _section("First 10 Customers", brief.get("first_ten_customers", []))
    _section("Launch Channels", brief.get("channels", []))
    _section("Pricing", brief.get("pricing", ""))
    _section("Landing Page Headline", brief.get("landing_headline", ""))
    _section("Cold Open Message", brief.get("cold_open", ""))
    _section("Launch Checklist", brief.get("launch_checklist", []))

    backend = brief.get("_backend")
    if backend:
        lines.append(f"*Generated by {backend}*")

    return "\n".join(lines)
