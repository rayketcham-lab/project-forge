"""Super Ideas - synthesize ambitious mega-projects from clusters of related ideas.

Takes all existing ideas, finds natural clusters/themes, and generates
"super projects" that combine multiple ideas into cohesive, meaningful,
real-world platforms.
"""

import hashlib
import logging
import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

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


_NAME_STOP_WORDS = frozenset({
    # Prepositions / articles
    "a", "an", "the", "and", "or", "for", "with", "in", "of", "on", "to", "by",
    "via", "from", "its", "this", "that", "into", "onto", "over",
    # Generic tech nouns (too common to be distinctive)
    "tool", "system", "platform", "suite", "engine", "hub",
    "service", "solution", "manager", "tracker", "analyzer",
    "monitor", "checker", "scanner", "detector", "reporter",
    "generator", "builder", "advisor", "assistant", "helper",
    "framework", "library", "module", "plugin", "agent",
    # Generic adjectives / catch-alls
    "automated", "automatic", "automation", "unified", "combined",
    "integrated", "advanced", "smart", "intelligent", "secure",
    "open", "source", "based",
})

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
            if w not in _NAME_STOP_WORDS and len(w) > 3:
                freq[w] += 1

        # Name words get double weight — they're the distilled concept
        for w in re.findall(r"[a-z]+", idea.name.lower()):
            if w not in _NAME_STOP_WORDS and len(w) > 3:
                freq[w] += 2

    return [w for w, _ in freq.most_common(8)]


def _dynamic_cluster_name(ideas: list["Idea"], categories: frozenset) -> str:  # noqa: F821
    """Generate a content-driven cluster name from component ideas."""
    keywords = _extract_cluster_keywords(ideas)
    suffix = random.choice(_SYNTHESIS_SUFFIXES)

    if len(keywords) >= 2:
        k1, k2 = keywords[0].title(), keywords[1].title()
        return random.choice([
            f"{k1} & {k2} {suffix}",
            f"{k1}-{k2} {suffix}",
            f"{k1} {k2} {suffix}",
        ])
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


def synthesize_super_idea(cluster: dict) -> SuperIdea:
    """Synthesize a super idea from a cluster of related ideas."""
    theme = cluster["theme"]
    ideas = cluster["ideas"]
    categories = list(cluster["categories"])

    # Combine descriptions
    component_summaries = []
    all_tech = set()
    for idea in ideas:
        component_summaries.append(f"- **{idea.name}**: {idea.tagline}")
        all_tech.update(idea.tech_stack)

    description = (
        f"{theme} brings together {len(ideas)} complementary project concepts into a single, "
        f"cohesive platform:\n\n"
        + "\n".join(component_summaries)
        + f"\n\nTogether, these components create something greater than the sum of their parts -- "
        f"a platform that addresses the full lifecycle of "
        f"{', '.join(c.value for c in categories)} challenges."
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
]


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

        # Pick the best cluster that overlaps with seed categories
        best = None
        for cluster in clusters:
            cat_overlap = cluster["categories"] & seed_cats
            if cat_overlap or best is None:
                best = cluster
                if cat_overlap:
                    break

        if not best:
            return None

        si = synthesize_super_idea(best)
        # Tag with the perspective
        si.description += f"\n\n**Perspective:** {label} — synthesized through the lens of {perspective}."

        # Check for duplicate base names in DB — skip if already exists
        import re

        existing = await self.db.list_ideas(limit=2000)
        existing_base_names = set()
        for ex in existing:
            if ex.name.startswith("[SUPER]"):
                raw = ex.name.replace("[SUPER] ", "")
                base = re.sub(r"\s*\([^)]+\)\s*$", "", raw).strip()
                existing_base_names.add(base.lower())

        if si.name.lower() in existing_base_names:
            logger.info("Skipping duplicate super idea: %s (base name exists)", si.name)
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
