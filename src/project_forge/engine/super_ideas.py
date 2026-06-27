"""Super Ideas - synthesize ambitious mega-projects from clusters of related ideas.

Takes all existing ideas, finds natural clusters/themes, and generates
"super projects" that combine multiple ideas into cohesive, meaningful,
real-world platforms.
"""

import hashlib
import logging
import os
import random
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


def _reasoning_llm_call() -> Callable[[str], str] | None:
    """Construct a callable that sends a prompt to an LLM and returns text.

    Uses the pluggable backend resolver — Anthropic API direct (when
    ANTHROPIC_API_KEY is set) OR Claude Code CLI shell-out (when `claude`
    is on PATH). Returns None when neither is available — caller falls
    back to slot-fill in that case.
    """
    from project_forge.engine.llm_backend import resolve_backend

    backend = resolve_backend()
    if backend is None:
        logger.info("FORGE_SUPER_REASONING set but no LLM backend — slot-fill fallback")
        return None

    logger.info("Super reasoning using backend: %s", backend.name)

    def _call(prompt: str) -> str:
        text = backend.call(prompt) or ""
        # Strip markdown fence if present (responses sometimes wrap JSON)
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]
        return text.strip()

    return _call


# Re-export so monkeypatch can target this module symbol in tests
__all__ = ["_reasoning_llm_call"]

# Cluster themes: map category pairs to meaningful platform concepts
THEME_TEMPLATES = {
    frozenset({IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.CRYPTO_INFRASTRUCTURE}): [
        "Post-Quantum PKI Platform",
        "Quantum-Safe Certificate Authority Suite",
        "PQC Crypto Operations Center",
    ],
    frozenset({IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.RFC_SECURITY}): [
        "PQC Standards Compliance Platform",
        "Post-Quantum Protocol Verification Suite",
        "Quantum-Ready RFC Implementation Hub",
    ],
    frozenset({IdeaCategory.PQC_CRYPTOGRAPHY, IdeaCategory.NIST_STANDARDS}): [
        "NIST PQC Transition Accelerator",
        "Federal Quantum Migration Platform",
        "PQC FIPS Compliance Toolkit",
    ],
    frozenset({IdeaCategory.NIST_STANDARDS, IdeaCategory.COMPLIANCE}): [
        "Federal Compliance Automation Platform",
        "NIST-to-Cloud Compliance Engine",
        "Continuous Authority-to-Operate Platform",
    ],
    frozenset({IdeaCategory.SECURITY_TOOL, IdeaCategory.VULNERABILITY_RESEARCH}): [
        "Autonomous Security Testing Platform",
        "Proactive Threat Discovery Engine",
        "Security Research Automation Suite",
    ],
    frozenset({IdeaCategory.DEVOPS_TOOLING, IdeaCategory.OBSERVABILITY}): [
        "Full-Stack DevOps Intelligence Platform",
        "Engineering Productivity Observatory",
        "Developer Experience Analytics Engine",
    ],
    frozenset({IdeaCategory.PRIVACY, IdeaCategory.COMPLIANCE}): [
        "Privacy-First Compliance Platform",
        "Data Governance Automation Suite",
        "Global Privacy Operations Center",
    ],
    frozenset({IdeaCategory.RFC_SECURITY, IdeaCategory.CRYPTO_INFRASTRUCTURE}): [
        "Standards-Driven PKI Platform",
        "RFC-Compliant Crypto Infrastructure",
        "Certificate Standards Verification Hub",
    ],
    frozenset({IdeaCategory.SECURITY_TOOL, IdeaCategory.DEVOPS_TOOLING}): [
        "DevSecOps Unified Platform",
        "Security-Embedded CI/CD Suite",
        "Shift-Left Security Intelligence",
    ],
    frozenset({IdeaCategory.AUTOMATION, IdeaCategory.COMPLIANCE}): [
        "Compliance Automation Engine",
        "Regulatory Response Platform",
        "Audit Intelligence Suite",
    ],
}

VISION_TEMPLATES = [
    (
        "Become the industry standard for {theme_lower} by unifying {count} critical "
        "capabilities into a single platform that organizations can deploy in weeks, not months."
    ),
    (
        "Create an open-source {theme_lower} that eliminates the fragmentation in today's "
        "tooling landscape. No more stitching together {count} different tools -- one platform."
    ),
    (
        "Build the platform that every CISO wishes existed: {theme_lower} that actually works "
        "together, with shared context and automated workflows across {count} integrated modules."
    ),
    (
        "Solve the {theme_lower} problem once and for all. Today's approach of {count} "
        "disconnected tools creates gaps. This platform closes them by design."
    ),
    (
        "Accelerate the industry's ability to tackle {theme_lower}. By combining {count} key "
        "capabilities, reduce what takes teams 6 months to a 2-week deployment."
    ),
]


_NAME_STOP_WORDS = frozenset(
    {
        # Prepositions / articles
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "with",
        "in",
        "of",
        "on",
        "to",
        "by",
        "via",
        "from",
        "its",
        "this",
        "that",
        "into",
        "onto",
        "over",
        # Generic tech nouns (too common to be distinctive)
        "tool",
        "system",
        "platform",
        "suite",
        "engine",
        "hub",
        "service",
        "solution",
        "manager",
        "tracker",
        "analyzer",
        "monitor",
        "checker",
        "scanner",
        "detector",
        "reporter",
        "generator",
        "builder",
        "advisor",
        "assistant",
        "helper",
        "framework",
        "library",
        "module",
        "plugin",
        "agent",
        # Generic adjectives / catch-alls
        "automated",
        "automatic",
        "automation",
        "unified",
        "combined",
        "integrated",
        "advanced",
        "smart",
        "intelligent",
        "secure",
        "open",
        "source",
        "based",
        "simple",
        "single",
        "multi",
        "cross",
        # Qualifiers that make nonsense names ("Well Known Defense Suite")
        "well",
        "known",
        "common",
        "general",
        "basic",
        "native",
        # OWASP / security jargon — appear in idea names but make bad super-names
        "insecure",
        "direct",
        "object",
        "broken",
        "sensitive",
        "exposure",
        "injection",
        "failure",
        "access",
        "control",
        "bypass",
        "escalation",
        "privilege",
        "attack",
        "threat",
        "weakness",
        # Generic action verbs in idea names
        "using",
        "detect",
        "enable",
        "enforce",
        "prevent",
        "handle",
        "improve",
        "extend",
        "support",
        "create",
        "update",
        "manage",
        # Generic tool-type nouns (describe WHAT it is, not WHAT it's about)
        "mapper",
        "parser",
        "proxy",
        "relay",
        "store",
        "queue",
        "cache",
        # Super-idea marker word — must not contaminate keyword extraction
        "super",
    }
)

_SYNTHESIS_SUFFIXES = [
    "Intelligence Center",
    "Operations Center",
    "Defense Suite",
    "Governance Engine",
    "Observatory",
    "Command Center",
    "Analysis Hub",
    "Enforcement Suite",
    "Discovery Engine",
    "Lifecycle Platform",
    "Security Intelligence",
    "Automation Hub",
]


def _extract_cluster_keywords(ideas: list["Idea"]) -> list[str]:  # noqa: F821 (forward ref)
    """Extract the most distinctive keywords from a set of component ideas."""
    freq: Counter[str] = Counter()

    for idea in ideas:
        # Tagline concept (before colon) gives the best signal
        tagline_part = idea.tagline.split(":")[0] if ":" in idea.tagline else idea.tagline
        for w in re.findall(r"[a-z]+", tagline_part.lower()):
            if w not in _NAME_STOP_WORDS and len(w) >= 5:
                freq[w] += 1

        # Name words get double weight — they're the distilled concept
        for w in re.findall(r"[a-z]+", idea.name.lower()):
            if w not in _NAME_STOP_WORDS and len(w) >= 5:
                freq[w] += 2

    return [w for w, _ in freq.most_common(8)]


def _dynamic_cluster_name(ideas: list["Idea"], categories: frozenset) -> str:  # noqa: F821
    """Generate a content-driven cluster name from component ideas."""
    keywords = _extract_cluster_keywords(ideas)
    suffix = random.choice(_SYNTHESIS_SUFFIXES)

    if len(keywords) >= 2:
        k1, k2 = keywords[0].title(), keywords[1].title()
        return random.choice(
            [
                f"{k1} & {k2} {suffix}",
                f"{k1} {k2} {suffix}",
            ]
        )
    elif keywords:
        return f"{keywords[0].title()} {suffix}"

    # Last resort: use category names (not "Unified Platform")
    cat_names = sorted(c.value.replace("-", " ").title() for c in categories)
    return f"{' & '.join(cat_names[:2])} {suffix}"


def _build_super_tagline(ideas: list["Idea"]) -> str:  # noqa: F821
    """Build a capability-specific tagline from component concept terms."""
    concepts: list[str] = []
    for idea in ideas[:3]:
        if ":" in idea.tagline:
            core = idea.tagline.split(":")[0].strip()
        else:
            # Use first 4 words of name (skip any [SUPER] prefix)
            words = [w for w in idea.name.split() if not w.startswith("[")][:4]
            core = " ".join(words)
        if 4 < len(core) < 45:
            concepts.append(core)

    synthesis_phrases = [
        "synthesized into one platform",
        "unified end-to-end",
        "integrated for full lifecycle coverage",
        "combined into a single platform",
        "in one unified system",
    ]

    if len(concepts) >= 2:
        phrase = random.choice(synthesis_phrases)
        return f"{concepts[0]} + {concepts[1]}: {phrase}"[:120]
    elif concepts:
        phrase = random.choice(synthesis_phrases)
        return f"{concepts[0]}: {phrase}"[:120]

    return f"{len(ideas)}-capability synthesis: end-to-end platform"[:120]


class SuperIdea(BaseModel):
    id: str = Field(default_factory=lambda: hashlib.sha256(str(datetime.now(UTC)).encode()).hexdigest()[:12])
    name: str
    tagline: str
    description: str
    vision: str
    component_idea_ids: list[str]
    categories_spanned: list[IdeaCategory]
    combined_feasibility: float = Field(ge=0.0, le=1.0)
    impact_score: float = Field(ge=0.0, le=1.0)
    tech_stack: list[str] = Field(default_factory=list)
    mvp_phases: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_super: bool = True


def find_idea_clusters(ideas: list[Idea], min_cluster_size: int = 2) -> list[dict]:
    """Find natural clusters of related ideas by category overlap and keyword affinity."""
    # Exclude [SUPER] ideas — they must not contaminate keyword extraction or taglines
    ideas = [i for i in ideas if not i.name.startswith("[SUPER]")]

    # Group by category pairs
    category_groups: dict[frozenset, list[Idea]] = defaultdict(list)
    for idea in ideas:
        category_groups[frozenset({idea.category})].append(idea)

    # Build cross-category clusters from theme templates
    clusters = []
    used_ideas: set[str] = set()

    for cat_pair, _templates in THEME_TEMPLATES.items():
        matching_ideas = []
        for idea in ideas:
            if idea.category in cat_pair and idea.id not in used_ideas:
                matching_ideas.append(idea)

        if len(matching_ideas) >= min_cluster_size:
            # Take the best scoring ideas for this cluster
            matching_ideas.sort(key=lambda i: i.feasibility_score, reverse=True)
            cluster_ideas = matching_ideas[: min(6, len(matching_ideas))]
            theme = _dynamic_cluster_name(cluster_ideas, cat_pair)
            clusters.append({"theme": theme, "ideas": cluster_ideas, "categories": cat_pair})
            for i in cluster_ideas:
                used_ideas.add(i.id)

    # Also cluster by single category if there are many ideas
    for cat in IdeaCategory:
        cat_ideas = [i for i in ideas if i.category == cat and i.id not in used_ideas]
        if len(cat_ideas) >= 3:
            cat_ideas.sort(key=lambda i: i.feasibility_score, reverse=True)
            cluster_ideas = cat_ideas[:5]
            theme = _dynamic_cluster_name(cluster_ideas, frozenset({cat}))
            clusters.append({"theme": theme, "ideas": cluster_ideas, "categories": frozenset({cat})})
            for i in cluster_ideas:
                used_ideas.add(i.id)

    clusters.sort(key=lambda c: sum(i.feasibility_score for i in c["ideas"]) / len(c["ideas"]), reverse=True)
    return clusters


def synthesize_super_idea(
    cluster: dict,
    *,
    use_reasoning: bool = False,
    llm_call=None,
) -> SuperIdea:
    """Synthesize a super idea from a cluster of related ideas.

    When use_reasoning=True and llm_call is provided, the cluster name is
    derived from reason_cluster_name (LLM proposes the unifying capability
    gap) instead of the slot-fill template. Falls back to slot-fill on LLM
    failure. Either way, the cluster signature is embedded in the
    description as [CLUSTER:<sig>] for signature-based dedup.
    """
    from project_forge.engine.super_reasoning import (
        cluster_signature,
        encode_cluster_tag,
        reason_cluster_name,
    )

    theme = cluster["theme"]
    ideas = cluster["ideas"]
    categories = list(cluster["categories"])

    # Try LLM-derived name when reasoning is on; fall back to theme on None.
    if use_reasoning and llm_call is not None:
        proposed = reason_cluster_name(ideas, llm_call=llm_call)
        if proposed:
            theme = proposed

    # Combine descriptions
    component_summaries = []
    all_tech = set()
    for idea in ideas:
        component_summaries.append(f"- **{idea.name}**: {idea.tagline}")
        all_tech.update(idea.tech_stack)

    sig = cluster_signature(ideas)
    description = (
        f"{theme} brings together {len(ideas)} complementary project concepts into a single, "
        f"cohesive platform:\n\n"
        + "\n".join(component_summaries)
        + f"\n\nTogether, these components create something greater than the sum of their parts -- "
        f"a platform that addresses the full lifecycle of "
        f"{', '.join(c.value for c in categories)} challenges."
        f"\n\n{encode_cluster_tag(sig)}"
    )

    tagline = _build_super_tagline(ideas)

    # Vision
    vision_template = random.choice(VISION_TEMPLATES)
    vision = vision_template.format(theme_lower=theme.lower(), count=len(ideas))

    # MVP phases - one per component idea
    mvp_phases = []
    for i, idea in enumerate(ideas):
        mvp_phases.append(f"Phase {i + 1}: {idea.name} - {idea.mvp_scope[:80]}")

    # Scores
    avg_feasibility = sum(i.feasibility_score for i in ideas) / len(ideas)
    # Impact is higher because combining ideas creates more value
    impact = min(0.98, avg_feasibility * 1.15)

    # Deduplicate tech stack, keep most common
    tech_counts: dict[str, int] = defaultdict(int)
    for t in all_tech:
        tech_counts[t] += 1
    tech_stack = sorted(tech_counts.keys(), key=lambda t: -tech_counts[t])[:6]

    return SuperIdea(
        name=theme,
        tagline=tagline[:120],
        description=description,
        vision=vision,
        component_idea_ids=[i.id for i in ideas],
        categories_spanned=categories,
        combined_feasibility=round(avg_feasibility, 2),
        impact_score=round(impact, 2),
        tech_stack=tech_stack,
        mvp_phases=mvp_phases,
    )


# 5 daily slots, each with a different category focus lens.
# The slot index rotates through these perspectives so each
# time-of-day run sees the idea pool through a different filter.
DAILY_ROTATION = [
    {
        "slot": 0,
        "label": "PQC & Crypto",
        "seed_categories": {
            IdeaCategory.PQC_CRYPTOGRAPHY,
            IdeaCategory.CRYPTO_INFRASTRUCTURE,
        },
        "perspective": "post-quantum migration and cryptographic operations",
    },
    {
        "slot": 1,
        "label": "Standards & Compliance",
        "seed_categories": {
            IdeaCategory.NIST_STANDARDS,
            IdeaCategory.COMPLIANCE,
            IdeaCategory.RFC_SECURITY,
        },
        "perspective": "standards compliance, regulatory automation, and RFC implementation",
    },
    {
        "slot": 2,
        "label": "Attack & Defense",
        "seed_categories": {
            IdeaCategory.SECURITY_TOOL,
            IdeaCategory.VULNERABILITY_RESEARCH,
        },
        "perspective": "offensive security testing and defensive tooling",
    },
    {
        "slot": 3,
        "label": "Platform & DevOps",
        "seed_categories": {
            IdeaCategory.DEVOPS_TOOLING,
            IdeaCategory.OBSERVABILITY,
            IdeaCategory.AUTOMATION,
        },
        "perspective": "developer experience, infrastructure, and operational excellence",
    },
    {
        "slot": 4,
        "label": "Privacy & Market",
        "seed_categories": {
            IdeaCategory.PRIVACY,
            IdeaCategory.MARKET_GAP,
        },
        "perspective": "privacy-preserving technology and untapped market opportunities",
    },
    # v0.12 — scope expansion slots beyond IT/security.
    {
        "slot": 5,
        "label": "Money & Automation",
        "seed_categories": {
            IdeaCategory.AUTOMATION_INCOME,
            IdeaCategory.AUTOMATION,
        },
        "perspective": "legal scalable automation for revenue — content engines, niche SaaS, lead-gen, outreach",
    },
    {
        "slot": 6,
        "label": "Consumer & Productivity",
        "seed_categories": {
            IdeaCategory.CONSUMER_APP,
            IdeaCategory.PRODUCTIVITY,
        },
        "perspective": "everyday apps and personal/work productivity that flips daily behavior",
    },
    {
        "slot": 7,
        "label": "Creator Economy",
        "seed_categories": {
            IdeaCategory.CREATOR_TOOLS,
            IdeaCategory.MARKET_GAP,
        },
        "perspective": (
            "tools for creators making content — writing, audio, video, newsletters, social, design, education"
        ),
    },
    # v0.15 — frontier / Claude-ecosystem slots. Push to 35th-century
    # framing: ideas where someone reading the pitch wants to drop
    # everything and build it.
    {
        "slot": 8,
        "label": "Claude Frontier",
        "seed_categories": {
            IdeaCategory.CLAUDE_SKILLS_AGENTS,
            IdeaCategory.AI_MARKETPLACE,
        },
        "perspective": (
            "frontier ideas for the Claude / agent ecosystem — skills, "
            "sub-agents, MCP servers, marketplaces, attribution, "
            "discovery. Ambitious enough to redefine how agents and "
            "their authors trade value"
        ),
    },
    # v0.16 — paid-product money slot + the rest of the agent-ecosystem.
    {
        "slot": 9,
        "label": "Paid Products & Vertical SaaS",
        "seed_categories": {
            IdeaCategory.MICRO_SAAS,
            IdeaCategory.VERTICAL_SAAS,
            IdeaCategory.ECOMMERCE_TOOLS,
            IdeaCategory.FINTECH_TOOLS,
        },
        "perspective": (
            "fundable, shippable software with recurring revenue — focused "
            "micro-SaaS, deep vertical software for underserved trades, "
            "seller operations, and finance ops. One paying buyer, clear "
            "Stripe button, no money-transmitter or financial-advice scope"
        ),
    },
    {
        "slot": 10,
        "label": "Agent Platform & Safety",
        "seed_categories": {
            IdeaCategory.AGENT_INFRA,
            IdeaCategory.CLAUDE_EVALS,
            IdeaCategory.AGENT_SECURITY,
            IdeaCategory.CONTEXT_MEMORY,
        },
        "perspective": (
            "the layers that make agents production-grade — the runtime "
            "that runs a fleet cheaply and durably, the evals that prove "
            "they work, the security that treats them as an attack surface, "
            "and the memory that gives them continuity"
        ),
    },
]


async def pick_least_covered_slot(db: Database) -> int:
    """Pick the DAILY_ROTATION slot whose seed_categories are most
    under-represented among active [SUPER] ideas.

    Replaces the original `hour % 5` rotation, which mechanically revisited
    each slot regardless of saturation and let the corpus pile up on the
    densest themes. Tiebreak on lowest slot index for deterministic output.
    """
    cur = await db.db.execute(
        "SELECT category, COUNT(*) c FROM ideas "
        "WHERE name LIKE '[SUPER]%' "
        "AND status NOT IN ('archived', 'rejected') "
        "GROUP BY category"
    )
    rows = await cur.fetchall()
    per_cat: dict[str, int] = {r["category"]: int(r["c"]) for r in rows}

    best_slot = 0
    best_count = None
    for slot, lens in enumerate(DAILY_ROTATION):
        # Count supers across this slot's seed categories.
        slot_count = sum(per_cat.get(c.value, 0) for c in lens["seed_categories"])
        if best_count is None or slot_count < best_count:
            best_count = slot_count
            best_slot = slot
    return best_slot


class SuperIdeaGenerator:
    def __init__(self, db: Database):
        self.db = db

    async def _store_super(self, si: SuperIdea) -> None:
        """Store a super idea as a regular idea in the DB."""
        idea = Idea(
            id=si.id,
            name=f"[SUPER] {si.name}",
            tagline=si.tagline,
            description=si.description + f"\n\n**Vision:** {si.vision}",
            category=(si.categories_spanned[0] if si.categories_spanned else IdeaCategory.SECURITY_TOOL),
            market_analysis=si.vision,
            feasibility_score=si.combined_feasibility,
            mvp_scope="\n".join(si.mvp_phases),
            tech_stack=si.tech_stack,
            status="new",
        )
        from project_forge.engine.dedup import filter_and_save

        await filter_and_save(idea, self.db)

    async def generate(self, count: int = 5) -> list[SuperIdea]:
        """Generate super ideas by clustering and synthesizing all ideas."""
        all_ideas = await self.db.list_ideas(limit=1000)
        if len(all_ideas) < 10:
            logger.warning(
                "Not enough ideas for super synthesis (need 10+, have %d)",
                len(all_ideas),
            )
            return []

        clusters = find_idea_clusters(all_ideas)
        supers = []
        used_names: set[str] = set()

        for cluster in clusters:
            if len(supers) >= count:
                break
            si = synthesize_super_idea(cluster)
            if si.name in used_names:
                continue
            used_names.add(si.name)
            await self._store_super(si)
            supers.append(si)
            logger.info(
                "Super idea: %s (impact: %.2f, %d components)",
                si.name,
                si.impact_score,
                len(si.component_idea_ids),
            )

        return supers

    async def generate_seeded(self, slot: int = 0) -> SuperIdea | None:
        """Generate ONE super idea using a rotated category seed.

        Each slot focuses on a different category lens so that running
        at different times of day produces different perspectives.
        """
        rotation = DAILY_ROTATION[slot % len(DAILY_ROTATION)]
        seed_cats = rotation["seed_categories"]
        perspective = rotation["perspective"]
        label = rotation["label"]

        all_ideas = await self.db.list_ideas(limit=1000)
        if len(all_ideas) < 10:
            return None

        # Weight the pool: seed categories get full weight,
        # others contribute at half probability for cross-pollination
        weighted: list[Idea] = []
        for idea in all_ideas:
            if idea.category in seed_cats:
                weighted.append(idea)
            elif random.random() < 0.3:
                weighted.append(idea)

        if len(weighted) < 6:
            weighted = all_ideas  # fallback to full pool

        clusters = find_idea_clusters(weighted)
        if not clusters:
            return None

        # Order clusters: seed-category overlap first, then everything else,
        # preserving the find_idea_clusters score order within each group.
        # We then iterate this list and try each cluster — skipping any whose
        # cluster_signature is already covered (when reasoning is on). Without
        # iteration, growth stalls on a stable corpus because the same top
        # cluster always wins and dedup blocks it forever.
        from project_forge.engine.super_reasoning import (
            cluster_signature,
            find_super_by_signature,
        )

        ordered = sorted(
            clusters,
            key=lambda c: 0 if c["categories"] & seed_cats else 1,
        )

        use_reasoning = bool(os.environ.get("FORGE_SUPER_REASONING"))
        llm_call = _reasoning_llm_call() if use_reasoning else None

        # Pre-skip clusters whose signature is already covered (cheap check
        # before we burn an LLM call on synthesizing a name).
        attempted = 0
        si = None
        chosen_cluster = None
        for cluster in ordered:
            attempted += 1
            if attempted > 6:
                # Bound the work — 6 attempts is plenty.
                break
            if use_reasoning:
                sig = cluster_signature(cluster["ideas"])
                if sig and await find_super_by_signature(self.db, sig) is not None:
                    logger.info(
                        "Cluster signature %s already covered — walking to next cluster",
                        sig,
                    )
                    continue
            candidate = synthesize_super_idea(
                cluster,
                use_reasoning=use_reasoning,
                llm_call=llm_call,
            )
            # Quality gate: reject if tagline is the N-capability-synthesis fallback.
            if re.match(r"^\d+-capability synthesis:", candidate.tagline):
                logger.info(
                    "Skipping super idea %s: tagline fell back to generic synthesis "
                    "(poor cluster quality) — walking to next cluster",
                    candidate.name,
                )
                continue
            si = candidate
            chosen_cluster = cluster
            break

        if si is None or chosen_cluster is None:
            return None

        # Tag with the perspective
        si.description += f"\n\n**Perspective:** {label} — synthesized through the lens of {perspective}."

        # Dedup strategy depends on whether reasoning is on:
        #
        # Reasoning ON: cluster_signature is the only authoritative dedup.
        #   The LLM produces unbounded names AND the tagline is built from
        #   the first member idea's tagline (which often shares concepts
        #   with existing supers even when the cluster signature is novel).
        #   Running base-name + tagline-primary-concept dedup on top
        #   over-blocks — every new combination involving a previously-seen
        #   idea would be rejected. Trust the signature.
        #
        # Reasoning OFF: fall through to the legacy belt-and-suspenders
        #   chain (base-name + tagline-primary) because slot-fill names
        #   collide too easily for signature alone to be safe.
        from project_forge.engine.super_reasoning import (
            extract_cluster_signature,
            find_super_by_signature,
        )

        candidate_sig = extract_cluster_signature(si.description)
        if use_reasoning and candidate_sig:
            existing_super = await find_super_by_signature(self.db, candidate_sig)
            if existing_super is not None:
                logger.info(
                    "Skipping super idea %s: cluster signature %s already covered by %s",
                    si.name,
                    candidate_sig,
                    existing_super.id,
                )
                return None
            # Reasoning path uses signature-only dedup — skip the legacy gates
            # below and proceed to store.
            await self._store_super(si)
            logger.info(
                "Seeded super [%s] (reasoning): %s (impact: %.2f, %d components)",
                label,
                si.name,
                si.impact_score,
                len(si.component_idea_ids),
            )
            return si

        from project_forge.engine.dedup import _super_base_name

        existing = await self.db.list_ideas(limit=2000)
        candidate_base = _super_base_name(f"[SUPER] {si.name}")
        existing_super_primaries: set[str] = set()
        for ex in existing:
            if not ex.name.startswith("[SUPER]"):
                continue
            if ex.status in ("archived", "rejected"):
                continue
            if _super_base_name(ex.name) == candidate_base:
                logger.info(
                    "Skipping duplicate super idea: %s (base '%s' covered by %s)",
                    si.name,
                    candidate_base,
                    ex.id,
                )
                return None
            # Also check tagline primary concept for near-duplicate coverage
            primary = ex.tagline.split(" + ")[0].split(":")[0].strip().lower()
            if primary and len(primary) > 5:
                existing_super_primaries.add(primary)

        new_primary = si.tagline.split(" + ")[0].split(":")[0].strip().lower()
        if new_primary and new_primary in existing_super_primaries:
            logger.info(
                "Skipping super idea %s: primary concept '%s' already covered",
                si.name,
                new_primary,
            )
            return None

        await self._store_super(si)
        logger.info(
            "Seeded super [%s]: %s (impact: %.2f, %d components)",
            label,
            si.name,
            si.impact_score,
            len(si.component_idea_ids),
        )
        return si
