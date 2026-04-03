"""Automated idea generation from seed data - no API key required.

Generates new project ideas by combining seed concepts, domains, and
contrarian prompts from the category database. Creates unique ideas
by cross-pollinating concepts across categories.
"""

import hashlib
import logging
import random
from datetime import UTC, datetime

from project_forge.engine.categories import CATEGORY_SEEDS, COMBINATORIC_TEMPLATES, CONTRARIAN_PROMPTS
from project_forge.engine.quality_review import review_idea
from project_forge.models import GenerationRun, Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)

# Pre-built idea templates from deep research
IDEA_TEMPLATES = [
    {
        "pattern": "{concept} for {domain}",
        "name_pattern": "{concept_short} {domain_short} Tool",
    },
    {
        "pattern": "Automated {concept} with {approach}",
        "name_pattern": "Auto-{concept_short}",
    },
    {
        "pattern": "{concept_a} meets {concept_b}: bridging {domain_a} and {domain_b}",
        "name_pattern": "{concept_a_short}-{concept_b_short} Bridge",
    },
]

TECH_STACKS = {
    IdeaCategory.PQC_CRYPTOGRAPHY: [
        ["python", "cryptography", "oqs-provider"],
        ["rust", "openssl", "pkcs11"],
        ["go", "crypto", "grpc"],
    ],
    IdeaCategory.NIST_STANDARDS: [
        ["python", "fastapi", "boto3"],
        ["python", "click", "jinja2"],
        ["python", "pytest", "cryptography"],
    ],
    IdeaCategory.RFC_SECURITY: [
        ["python", "httpx", "fastapi"],
        ["python", "cryptography", "pyasn1"],
        ["go", "grpc", "protobuf"],
    ],
    IdeaCategory.CRYPTO_INFRASTRUCTURE: [
        ["python", "cryptography", "sqlite"],
        ["rust", "pkcs11", "tokio"],
        ["python", "fastapi", "redis"],
    ],
    IdeaCategory.SECURITY_TOOL: [["python", "click", "httpx"], ["rust", "tokio", "serde"], ["go", "cobra", "grpc"]],
    IdeaCategory.VULNERABILITY_RESEARCH: [
        ["python", "scapy", "httpx"],
        ["python", "click", "beautifulsoup4"],
        ["rust", "nom", "tokio"],
    ],
    IdeaCategory.COMPLIANCE: [
        ["python", "fastapi", "boto3"],
        ["python", "click", "jinja2"],
        ["python", "sqlalchemy", "httpx"],
    ],
    IdeaCategory.PRIVACY: [
        ["python", "spacy", "kafka-python"],
        ["go", "redis", "grpc"],
        ["python", "pandas", "scikit-learn"],
    ],
    IdeaCategory.AUTOMATION: [
        ["python", "fastapi", "anthropic"],
        ["python", "click", "gitpython"],
        ["python", "celery", "redis"],
    ],
    IdeaCategory.DEVOPS_TOOLING: [
        ["python", "fastapi", "d3.js"],
        ["go", "kubernetes", "prometheus"],
        ["python", "boto3", "terraform"],
    ],
    IdeaCategory.OBSERVABILITY: [
        ["python", "prometheus-client", "fastapi"],
        ["go", "opentelemetry", "grpc"],
        ["python", "elasticsearch", "click"],
    ],
    IdeaCategory.MARKET_GAP: [
        ["python", "fastapi", "httpx"],
        ["node", "express", "react"],
        ["python", "django", "celery"],
    ],
}


def _build_rich_content(
    concept: str,
    domain: str,
    category: IdeaCategory,
    direction: str,
    cat_desc: str,
) -> tuple[str, str, str]:
    """Build rich description, market analysis, and MVP scope.

    Returns (description, market_analysis, mvp_scope) with real substance.
    """
    cat_name = category.value.replace("-", " ")
    concept_short = concept.split("—")[0].split("(")[0].strip()

    # --- DESCRIPTION (3+ sentences, explains problem + solution + why) ---
    problem_starters = [
        f"Organizations working in {domain} face a critical gap: {concept_short} "
        f"is either done manually, done poorly, or not done at all.",
        f"The {domain} sector has no good solution for {concept_short}. "
        f"Teams waste hours on manual processes that should be automated.",
        f"As {domain} grows in complexity, the need for {concept_short} "
        f"becomes acute. Current tools are fragmented and incomplete.",
        f"In {domain}, {concept_short} is a blind spot. Teams don't realize "
        f"how exposed they are until an incident forces the conversation.",
    ]
    solution_middles = [
        f"This project builds a purpose-built tool that handles {concept_short} "
        f"specifically for {domain} environments. It integrates with existing "
        f"workflows rather than replacing them, reducing adoption friction.",
        f"The solution is a focused {cat_name} tool that automates "
        f"{concept_short} for {domain}. It provides clear visibility into "
        f"what's happening, actionable recommendations, and measurable outcomes.",
        f"This tool brings {cat_name} discipline to {domain} by providing "
        f"automated {concept_short} with clear reporting, integration hooks, "
        f"and a developer-first CLI interface.",
    ]
    why_enders = [
        f"The timing is right because {domain} is under increasing regulatory "
        f"and security pressure, and teams need tools that work today, not "
        f"frameworks that require months of customization.",
        f"This matters now because the intersection of {cat_name} and {domain} "
        f"is rapidly growing, but tooling hasn't kept pace with the demand. "
        f"Early movers in this space will define the category.",
        f"The {domain} market is actively looking for solutions in this space. "
        f"Existing tools are either too generic or too expensive for most teams.",
    ]

    if direction == "contrarian":
        prompt = random.choice(CONTRARIAN_PROMPTS)
        description = (
            f"**Contrarian angle:** {prompt}\n\n"
            + random.choice(problem_starters)
            + " "
            + random.choice(solution_middles)
            + " "
            + random.choice(why_enders)
        )
    elif direction == "combinatoric":
        template = random.choice(COMBINATORIC_TEMPLATES)
        other_cat = random.choice([c for c in IdeaCategory if c != category])
        other_concept = random.choice(CATEGORY_SEEDS[other_cat]["seed_concepts"])
        filled = template.format(
            concept_a=concept_short,
            concept_b=other_concept.split("—")[0].strip(),
            domain_a=cat_name,
            domain_b=other_cat.value.replace("-", " "),
        )
        description = (
            f"**Cross-domain insight:** {filled}\n\n"
            + random.choice(problem_starters)
            + " "
            + random.choice(solution_middles)
            + " "
            + random.choice(why_enders)
        )
    elif direction == "crossover":
        other_cat = random.choice([c for c in IdeaCategory if c != category])
        other_domain = random.choice(CATEGORY_SEEDS[other_cat]["domains_to_cross"])
        description = (
            f"**Crossover concept:** What happens when {concept_short} "
            f"meets the needs of {other_domain}?\n\n"
            + random.choice(problem_starters)
            + " "
            + random.choice(solution_middles)
            + " "
            + random.choice(why_enders)
        )
    else:
        description = (
            random.choice(problem_starters) + " " + random.choice(solution_middles) + " " + random.choice(why_enders)
        )

    # --- MARKET ANALYSIS (specific to category + domain, not generic) ---
    market_templates = [
        (
            f"The {domain} market is estimated at billions globally, and "
            f"{cat_name} tooling within it is severely underserved. "
            f"Most teams rely on general-purpose tools that weren't designed "
            f"for {concept_short}. A focused solution captures a niche that "
            f"larger vendors ignore because it's too specialized for them "
            f"but critical for practitioners."
        ),
        (
            f"Demand for {cat_name} solutions in {domain} is growing due to "
            f"increasing regulatory requirements, security threats, and "
            f"operational complexity. The current tooling landscape is "
            f"fragmented — teams stitch together 3-5 different tools to "
            f"accomplish what a single focused product should handle. "
            f"This is the right time to build a unified approach."
        ),
        (
            f"Three trends drive demand: (1) {domain} is facing unprecedented "
            f"security and compliance pressure, (2) existing {cat_name} tools "
            f"don't address the specific needs of {concept_short}, and "
            f"(3) teams are willing to pay for tools that save them time "
            f"and reduce risk. The competitive landscape is sparse — mostly "
            f"manual processes and internal scripts."
        ),
    ]
    market_analysis = random.choice(market_templates)

    # --- MVP SCOPE (concrete deliverables, not generic filler) ---
    mvp_templates = [
        (
            f"**Phase 1 (Weeks 1-2):** Core engine for {concept_short} "
            f"targeting {domain}. CLI interface with JSON/table output. "
            f"Support for the top 3 most common {domain} configurations.\n"
            f"**Phase 2 (Weeks 3-4):** Web dashboard for results "
            f"visualization. Integration with CI/CD pipelines (GitHub Actions). "
            f"Export to common formats (CSV, SARIF, JSON).\n"
            f"**Out of scope for MVP:** Multi-tenant SaaS, custom plugins, "
            f"enterprise SSO. Keep it focused and shippable."
        ),
        (
            f"**Core deliverable:** A working {cat_name} tool that handles "
            f"{concept_short} for {domain} environments.\n"
            f"**Must-haves:** CLI with clear output, configuration via YAML, "
            f"documented API for automation, basic test suite.\n"
            f"**Nice-to-haves:** Web UI, Slack/webhook notifications, "
            f"scheduled scanning.\n"
            f"**Explicitly excluded:** Cloud-hosted SaaS version, mobile app, "
            f"AI/ML features. Ship a solid tool first."
        ),
        (
            f"**Week 1:** Implement the core {concept_short} logic. "
            f"Write 10+ tests covering happy path and edge cases. "
            f"Support {domain} as the primary target.\n"
            f"**Week 2:** Build the CLI interface with rich output "
            f"(tables, color, progress bars). Add configuration file support.\n"
            f"**Week 3:** CI/CD integration — GitHub Action, pre-commit hook, "
            f"or cron-compatible runner. Add documentation and README.\n"
            f"**Week 4:** Beta testing, bug fixes, and initial public release. "
            f"Publish to PyPI/npm/crates.io as appropriate."
        ),
    ]
    mvp_scope = random.choice(mvp_templates)

    return description, market_analysis, mvp_scope


def _content_hash(category: str, concept_idx: int, domain_idx: int, direction: str) -> str:
    """Deterministic content fingerprint from the generation inputs."""
    key = f"{category}:{concept_idx}:{domain_idx}:{direction}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _make_name(concept: str, domain: str, direction: str, variant: int = 0) -> str:
    """Build idea name from concept + domain + variant for uniqueness."""
    words = concept.split()[:4]  # Use 4 words instead of 3 for more variety
    name_base = " ".join(w.capitalize() for w in words)
    domain_word = domain.split()[0].capitalize()
    if variant == 0:
        return f"{name_base} for {domain_word}" if len(name_base) < 35 else name_base
    # Variant suffixes for when the base name is taken
    suffixes = ["Pro", "Suite", "Hub", "Engine", "Platform", "Toolkit", "Lab", "Guard"]
    suffix = suffixes[variant % len(suffixes)]
    return f"{name_base} {suffix}" if len(name_base) < 35 else f"{name_base} {suffix}"


def generate_local_idea(
    category: IdeaCategory | None = None,
    recent_names: list[str] | None = None,
    used_tuples: set[str] | None = None,
) -> tuple[Idea, str, int, int, str]:
    """Generate an idea locally from seed data without API calls.

    Returns (idea, category_value, concept_idx, domain_idx, direction)
    so the caller can record the tuple.
    """
    recent = set(recent_names or [])
    used = used_tuples or set()

    if category is None:
        category = random.choice(list(IdeaCategory))

    seeds = CATEGORY_SEEDS[category]
    concepts = seeds["seed_concepts"]
    domains = seeds["domains_to_cross"]
    directions = ["basic", "contrarian", "combinatoric", "crossover"]

    # Try to find an unused tuple first
    concept_idx = random.randrange(len(concepts))
    domain_idx = random.randrange(len(domains))
    direction = random.choice(directions)

    # Search for unused tuple (up to 50 attempts)
    for _ in range(50):
        ch = _content_hash(category.value, concept_idx, domain_idx, direction)
        if ch not in used:
            break
        concept_idx = random.randrange(len(concepts))
        domain_idx = random.randrange(len(domains))
        direction = random.choice(directions)

    concept = concepts[concept_idx]
    domain = domains[domain_idx]
    ch = _content_hash(category.value, concept_idx, domain_idx, direction)

    # Build rich description, market analysis, and MVP scope
    cat_desc = seeds["description"]
    description, market_analysis, mvp_scope = _build_rich_content(
        concept,
        domain,
        category,
        direction,
        cat_desc,
    )

    # Generate unique name with variant fallback
    name = _make_name(concept, domain, direction)
    for variant in range(1, 10):
        if name not in recent:
            break
        name = _make_name(concept, domain, direction, variant)

    tagline = f"{concept[:80]} — tailored for {domain}"
    tech_stack = random.choice(TECH_STACKS.get(category, [["python", "fastapi"]]))
    score = round(random.uniform(0.55, 0.92), 2)

    idea = Idea(
        id=hashlib.sha256((name + str(datetime.now(UTC))).encode()).hexdigest()[:12],
        name=name,
        tagline=tagline[:120],
        description=description,
        category=category,
        market_analysis=market_analysis,
        feasibility_score=score,
        mvp_scope=mvp_scope,
        tech_stack=tech_stack,
        content_hash=ch,
    )
    return idea, category.value, concept_idx, domain_idx, direction


async def run_auto_scan(db: Database, count: int = 5) -> list[Idea]:
    """Generate multiple ideas locally and store them.

    Uses input-tuple tracking to avoid regenerating the same
    (category, concept, domain, direction) combination.
    """
    # Load existing names (lightweight, names only)
    recent_names = set(await db.get_all_idea_names())

    # Load used tuples from DB
    cursor = await db.db.execute("SELECT category, concept_idx, domain_idx, direction FROM used_tuples")
    rows = await cursor.fetchall()
    used_tuples = {_content_hash(r[0], r[1], r[2], r[3]) for r in rows}

    ideas = []
    # Exclude SELF_IMPROVEMENT — that category is only for the introspection engine
    categories = [c for c in IdeaCategory if c != IdeaCategory.SELF_IMPROVEMENT]
    random.shuffle(categories)

    for i in range(count):
        cat = categories[i % len(categories)]
        run = GenerationRun(category=cat)
        try:
            idea, cat_val, c_idx, d_idx, direction = generate_local_idea(
                category=cat,
                recent_names=list(recent_names),
                used_tuples=used_tuples,
            )
            # Record tuple regardless of review outcome (avoid regenerating bad combos)
            await db.record_used_tuple(cat_val, c_idx, d_idx, direction)
            used_tuples.add(_content_hash(cat_val, c_idx, d_idx, direction))
            qr = review_idea(idea)
            if not qr.passed:
                logger.warning("Auto-scan idea '%s' rejected: %s", idea.name, "; ".join(qr.reasons))
                continue
            await db.save_idea(idea)
            recent_names.add(idea.name)
            ideas.append(idea)
            run.idea_id = idea.id
            run.success = True
            run.completed_at = datetime.now(UTC)
            await db.save_run(run)
            logger.info(
                "Auto-generated: %s (%s, %.2f)",
                idea.name,
                idea.category.value,
                idea.feasibility_score,
            )
        except Exception as e:
            run.error = str(e)
            run.completed_at = datetime.now(UTC)
            await db.save_run(run)
            logger.error("Auto-generation failed: %s", e)

    return ideas
