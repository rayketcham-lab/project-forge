"""LLM-first idea generator — the pivot away from template-fill.

The original `auto_scan.generate_local_idea` is mechanical: it picks a
template, fills in slots from CATEGORY_SEEDS, and emits an idea. After
4,800+ ideas the slots are saturated and the engine just paraphrases
itself ("drumming the same drum" — Ray, 2026-06-08).

This module does the opposite: ask Haiku 4.5 for a *whole* idea, with
three deliberate variety knobs feeding the prompt:

  1. MODE (one of 5)
     - novel       fresh problem-solution pair for the persona
     - inversion   take a paid SaaS, build the free / open-source version
     - bundle      combine 3 existing tools into a single focused product
     - microservice extract one 100-line micro-utility from a big tool
     - adversarial break an assumption everyone makes in this category

  2. PERSONA (rotated per category)
     Category-specific (indie hackers for money bots, CISOs for security
     tools, parents for consumer apps, etc.). Each cycle picks a fresh
     persona to keep the framing varied.

  3. ANTI-SIMILARITY
     30 most-recent active idea names + taglines from the same category
     are passed in with an explicit "do NOT produce anything resembling
     these" instruction. Pre-prevents the regrowth pattern that the
     INSERT-time dedup gates catch reactively.

Cost: ~$0.0024/call at Haiku pricing. At the 1h expand cadence that's
$0.058/day. The semantic-dedup tie-breaker fires on ~10% of borderline
cases so total LLM bill is roughly $2/month at default cadence.

Caller:
    result = await generate_idea_llm(db, category)
    if result is None:
        idea = template_fallback(category)
    else:
        await filter_and_save(result.idea, db)

Returns None when no backend is reachable or the LLM produces
unparseable output — caller falls back to the template path.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

from project_forge.engine.llm_backend import LLMBackend, resolve_cheap_backend
from project_forge.models import Idea, IdeaCategory
from project_forge.storage.db import Database

logger = logging.getLogger(__name__)


GENERATION_MODES = ["novel", "inversion", "bundle", "microservice", "adversarial"]


# ARTIFACT TYPES — orthogonal to MODE. Mode is the *thinking lens*
# (novel/inversion/bundle/…); artifact type is the *shape* of the
# output. Default behaviour: a project-pitch. For Claude Lab categories
# we rotate through 8 specific artifact shapes so Churn doesn't just
# produce 50 variants of the same "project pitch" — it produces ideas
# for skills, sub-agents, MCP servers, hooks, slash commands, workflows,
# protocols, and raw capability extensions.
ARTIFACT_TYPES = [
    "skill",          # .claude/skills/ entry — single-purpose, focused
    "sub-agent",      # .claude/agents/ entry — specialized agent w/ role
    "mcp-server",     # MCP protocol server exposing N tools
    "hook",           # Claude Code lifecycle hook (PreToolUse, etc.)
    "slash-command",  # /command in a project for a repeated workflow
    "workflow",       # composition of 2-4 sub-agents fanned for a task
    "protocol",       # standard/spec proposal for inter-agent comms
    "ability",        # raw capability extension — what the agent can NOW do
]

# Categories that get artifact-type variety. Everything else stays on
# the default project-pitch shape (artifact_type = None).
_ARTIFACT_ROTATION_CATEGORIES = {
    IdeaCategory.CLAUDE_SKILLS_AGENTS,
    IdeaCategory.AI_MARKETPLACE,
}


_ARTIFACT_PROMPTS: dict[str, str] = {
    "skill": (
        "Pitch this as a Claude Code SKILL (a `.claude/skills/{name}/` entry). "
        "Skills are single-purpose, focused capabilities the model invokes when "
        "the user's request matches. The pitch should specify: the trigger "
        "phrase / pattern, the deterministic instructions in SKILL.md (~50-200 "
        "lines), what files/tools the skill needs, and what the success signal "
        "looks like. Resist scope creep — one skill, one job."
    ),
    "sub-agent": (
        "Pitch this as a Claude SUB-AGENT (a `.claude/agents/{name}.md` entry "
        "spawnable via the Agent tool). Sub-agents are specialised personas "
        "with constrained tool access and a clear domain. The pitch should "
        "specify: the role + persona, when the parent agent should fan it out, "
        "which tools it gets (read-only? bash? edit?), and what artefact it "
        "returns to the parent. Aim for fanned-out parallelism, not sequential "
        "delegation."
    ),
    "mcp-server": (
        "Pitch this as an MCP (Model Context Protocol) SERVER. MCP servers "
        "expose a set of typed tools the agent can call. The pitch should "
        "specify: the 3-6 tools exposed (name + JSON schema for inputs / "
        "outputs), the transport (stdio / SSE), auth model, and what makes "
        "this server worth spinning up vs. shelling out. Concrete schemas, "
        "not vibes."
    ),
    "hook": (
        "Pitch this as a Claude Code HOOK (PreToolUse, PostToolUse, "
        "UserPromptSubmit, PreCompact, Stop, SessionStart, etc.). Hooks fire "
        "deterministically at lifecycle points. The pitch should specify: "
        "which hook event fires it, what the hook script does in 5-30 lines, "
        "what it blocks or allows, and the failure mode if it errors. Hooks "
        "are guardrails or augmentations — name which."
    ),
    "slash-command": (
        "Pitch this as a Claude Code SLASH COMMAND (a `.claude/commands/{name}.md` "
        "entry). Slash commands are user-invokable workflows for a repeated "
        "task in a specific project. The pitch should specify: the command "
        "name (`/foo`), the arguments it takes, what tools / sub-agents it "
        "drives, and the final output the user sees. Compare to existing "
        "commands so the niche is clear."
    ),
    "workflow": (
        "Pitch this as an AGENT WORKFLOW — a composition of 2-4 sub-agents "
        "or skills fanned out (parallel) or chained (sequential) for a "
        "multi-step task. The pitch should specify: the entry trigger, the "
        "agent graph (which agents run when, what data flows between them), "
        "the supervisor's role, and the verifiable output. Diagram the graph "
        "in text. Workflows must be reproducible, not stochastic."
    ),
    "protocol": (
        "Pitch this as a PROTOCOL / SPEC PROPOSAL for the agent ecosystem. "
        "Protocols are how agents, marketplaces, or tools talk to each other "
        "at scale. The pitch should specify: the problem the protocol solves, "
        "the message shape (JSON / wire format), versioning + extensibility "
        "rules, and reference implementations needed for adoption. Be explicit "
        "about who has to adopt it for it to work."
    ),
    "ability": (
        "Pitch this as a raw ABILITY EXTENSION — what the agent can NOW do "
        "that it couldn't before. Not a project, not a tool — a capability "
        "primitive. The pitch should specify: the gap in current capability, "
        "the minimum primitive (one function? one model? one dataset?) that "
        "closes the gap, what becomes possible downstream once this primitive "
        "exists, and the cheapest experiment that proves the primitive works."
    ),
}


_MODE_PROMPTS: dict[str, str] = {
    "novel": (
        "Pitch a fresh project idea that solves a SPECIFIC problem this "
        "persona feels acutely. The idea should be concrete enough that "
        "you could draw a one-screen demo on a napkin."
    ),
    "inversion": (
        "Pick a paid SaaS or commercial tool this persona is currently "
        "stuck paying for, where the underlying engine is actually simple. "
        "Pitch the open-source / self-hosted / free version that wins on "
        "control, transparency, or price."
    ),
    "bundle": (
        "This persona is paying for 3+ overlapping tools whose features "
        "could plausibly live in one focused product. Pick a real combination "
        "and pitch the unified product. Be specific about which existing "
        "tools you're consolidating."
    ),
    "microservice": (
        "Take a big complex tool this persona uses and extract ONE tiny "
        "100-line utility that does ONE thing better than the parent product. "
        "The idea should be brutally narrow — a Unix-philosophy single-job tool."
    ),
    "adversarial": (
        "Identify one assumption that everyone working in this category "
        "takes for granted but is actually wrong (or rapidly becoming wrong). "
        "Pitch the project that exploits the gap created by that wrong "
        "assumption. State the assumption explicitly in the description."
    ),
}


# Personas per category. Keys present here override the generic fallback;
# fall-through goes to a category-agnostic operator persona so we still
# generate something coherent. Each persona is one line: role + pain.
PERSONAS_BY_CATEGORY: dict[IdeaCategory, list[str]] = {
    IdeaCategory.AUTOMATION_INCOME: [
        "indie hacker chasing $5k/month MRR with one solo product",
        "newsletter operator with 8k subscribers, $200/mo Substack bill, no audience-targeting tools",
        "Etsy seller doing $30k/yr, manual listing optimization eats Sunday nights",
        "creator with 50k followers, monetization is brand deals only, hates the inconsistency",
        "freelance agency owner, 4 clients, struggling to standardize lead-gen across them",
        "side-hustler with a day job, 5 hours per weekend, needs deployable-in-a-weekend tools",
        "POD seller on Printify, churning niche designs, manual trend research is the bottleneck",
        "course creator with 200 students, retention is bad, has no signal on where they drop off",
        "B2B affiliate, ranks for 5 keywords, content production is the throttle",
        "ecommerce dropshipper $80k/yr who needs to graduate to a real brand",
    ],
    IdeaCategory.CONSUMER_APP: [
        "parent of two under 8, juggling soccer schedules and pediatrician appointments",
        "renter sharing a small apartment with two roommates, kitchen logistics are a daily fight",
        "remote worker in a new city, wants to meet people but social apps are exhausting",
        "ADHD adult who can't make pomodoro stick, needs body-doubling but lives alone",
        "first-time homebuyer overwhelmed by inspection reports and contractor estimates",
        "elderly daughter coordinating her mother's medications across three doctors",
        "couple negotiating shared finances without merging accounts",
        "fitness enthusiast tracking macros, hates that MyFitnessPal sold to AdTech",
        "small-town homeowner with a giant lawn and seasonal storm damage",
        "queer person navigating partner visits + healthcare in a hostile state",
    ],
    IdeaCategory.PRODUCTIVITY: [
        "consultant managing 7 client engagements, three timezones, calendar is a graveyard",
        "engineering manager of 12, weekly 1:1s + status reports + planning + escalations",
        "PhD student in year 4, writing dissertation while running experiments + TA-ing",
        "indie author drafting a novel between contract gigs",
        "head of growth at Series A, owns SEO + email + paid + analytics, only one report",
        "founder doing investor updates monthly, board prep quarterly, all from a Google Doc",
        "executive coach with 14 clients, needs to track each one's trajectory without CRM bloat",
        "researcher tracking 200 papers across 4 ongoing projects, citation manager is mush",
        "solo developer running 6 side projects + a day job",
        "remote PM running two product squads, async-first, drowning in Notion docs",
    ],
    IdeaCategory.CLAUDE_SKILLS_AGENTS: [
        "indie dev shipping Claude Code skills for their 5-person team, wants a skill that wins on day one",
        "platform engineer at a 200-person org, building MCP servers for internal infra, needs auth + audit baked in",
        "AI researcher prototyping agentic workflows, wants fanned-out sub-agents that compose without orchestration glue",
        "engineering manager who wants per-PR review sub-agents that learn from accepted/rejected diffs",
        "terminal power user automating their life: dotfiles + email + RSS + bills via Claude Code agents",
        "DevSecOps engineer building a sub-agent that grades code for safety + hallucination risk before merge",
        "ML engineer wanting Claude integrated into experiment tracking with auto-tagged regressions",
        "founder building an AI-first dev tool, looking for a primitive everyone will need but nobody has built",
        "consultant who wants reproducible agent workflows for clients — same input, same output, every time",
        "open-source maintainer building skills they wish existed for their own repo",
        "educator turning a CS curriculum into agent-led labs with auto-graded code reviews",
        "DevRel engineer building MCP demos for a platform team, needs each one to be impressive in 90 seconds",
    ],
    IdeaCategory.AI_MARKETPLACE: [
        "founder building 'the App Store for agents' — needs a discovery + payment + reputation layer that doesn't suck",
        "VC scouting AI marketplaces, wants to know which primitive everyone will pay for once it exists",
        "AI engineer at a platform company designing the agent layer that handles attribution + revenue split",
        "skill author wanting income from their distribution without building a whole storefront",
        "team lead at a 500-engineer org wanting skill governance: who installed what, where's the budget cap",
        "buyer comparing two agents for the same task, needs a trust signal that isn't 'GitHub stars'",
        "DevRel for an AI platform launching a marketplace, wants the right primitives shipped at GA",
        "compliance officer worried about agent provenance — who trained this, on what data, with what consent",
        "researcher studying agent quality signals, wants a measurement framework the market will respect",
        "educator teaching prompt engineering, wants an exemplar marketplace students can browse + learn from",
        "indie developer wanting to bundle 5 skills as 'my style' and rent them to others",
        "platform engineer building the rev-share rails so 3-agent collaborations distribute earnings fairly",
    ],
    IdeaCategory.CREATOR_TOOLS: [
        "podcaster releasing weekly, edits everything herself in Descript, hates the export step",
        "YouTube creator at 80k subs, thumbnails are the bottleneck — A/B testing is a fantasy",
        "TikTok creator producing 5/week, repurposing to YouTube Shorts is manual",
        "newsletter writer at 12k subs, posts every Thursday, no time for cross-platform promo",
        "indie game streamer trying to start a paid Discord, monetization is a maze",
        "music producer dropping a track a month, social-clip generation is a weekly side-quest",
        "comic artist serializing on Substack, needs to clip + post to Threads/IG",
        "course creator with 500 students, wants better completion analytics than Teachable provides",
        "writer juggling Substack + Medium + personal site, cross-posting eats 3 hours/week",
        "live-streamer who wants automated highlight reels for clip-farming subreddits",
    ],
    # Security categories still get their detailed personas via the
    # original PERSONA_SEEDS list (diversity_prompts.py). We pull from
    # there in _pick_persona() when the category is not above.
}


@dataclass
class LLMGenerationResult:
    """One successful LLM generation. None means caller should fall back."""

    idea: Idea
    mode: str
    persona: str
    backend: str
    raw_response: str
    # v0.15a — which artifact shape this draw produced. None for the
    # default project-pitch shape, one of ARTIFACT_TYPES for the
    # Claude Lab categories.
    artifact_type: str | None = None


# --------------------------------------------------------------------------- #
# Mode selection                                                              #
# --------------------------------------------------------------------------- #


async def pick_least_used_mode(db: Database, category: IdeaCategory) -> str:
    """Pick the GENERATION_MODES entry that has produced the fewest active
    ideas in this category. Stable tiebreak on the mode list order so the
    selection is deterministic given equal counts."""
    cur = await db.db.execute(
        "SELECT generation_mode, COUNT(*) c FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "AND generation_mode IS NOT NULL "
        "GROUP BY generation_mode",
        (category.value,),
    )
    rows = await cur.fetchall()
    counts = {r["generation_mode"]: int(r["c"]) for r in rows}
    return min(GENERATION_MODES, key=lambda m: (counts.get(m, 0), GENERATION_MODES.index(m)))


async def pick_least_used_artifact(db: Database, category: IdeaCategory) -> str:
    """Pick the ARTIFACT_TYPES entry with the fewest active ideas in this
    category. Same rotation discipline as pick_least_used_mode — over time
    every artifact shape gets equal airtime.
    """
    cur = await db.db.execute(
        "SELECT artifact_type, COUNT(*) c FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "AND artifact_type IS NOT NULL "
        "GROUP BY artifact_type",
        (category.value,),
    )
    rows = await cur.fetchall()
    counts = {r["artifact_type"]: int(r["c"]) for r in rows}
    return min(ARTIFACT_TYPES, key=lambda a: (counts.get(a, 0), ARTIFACT_TYPES.index(a)))


# --------------------------------------------------------------------------- #
# Persona selection                                                           #
# --------------------------------------------------------------------------- #


def _security_personas() -> list[str]:
    """Pull the PERSONA_SEEDS from diversity_prompts and flatten role+pain
    into single-line persona strings. Late import to keep this module's
    import time low.
    """
    from project_forge.engine.diversity_prompts import PERSONA_SEEDS

    return [f"{p['role']} — {p['pain']}" for p in PERSONA_SEEDS]


def _pick_persona(category: IdeaCategory) -> str:
    pool = PERSONAS_BY_CATEGORY.get(category)
    if pool is None:
        # Security and other categories fall back to the original seed pool.
        pool = _security_personas()
    return random.choice(pool)


# --------------------------------------------------------------------------- #
# Anti-similarity                                                             #
# --------------------------------------------------------------------------- #


async def _recent_idea_lines(
    db: Database, category: IdeaCategory, limit: int = 30,
) -> list[str]:
    """Return up to `limit` recent 'name — tagline' strings from the same
    category. Used as the do-not-produce list in the prompt."""
    cur = await db.db.execute(
        "SELECT name, tagline FROM ideas "
        "WHERE category = ? "
        "AND status NOT IN ('archived', 'rejected') "
        "ORDER BY generated_at DESC LIMIT ?",
        (category.value, limit),
    )
    rows = await cur.fetchall()
    return [f"- {r['name']} — {r['tagline']}" for r in rows]


# --------------------------------------------------------------------------- #
# Prompt building + parsing                                                   #
# --------------------------------------------------------------------------- #


_JSON_SCHEMA_INSTRUCTION = """
Respond with JSON only — no markdown wrapping, no commentary:
{
  "name": "Short pitchable name (3-6 words, title case)",
  "tagline": "One-line summary, max 100 chars, lowercase, concrete",
  "description": "2-3 sentence pitch. State the problem, the solution, why it matters now.",
  "market_analysis": "Who pays for this and why. Be specific about the buyer.",
  "mvp_scope": "Phase 1, Phase 2, Phase 3 — what you build in each.",
  "tech_stack": ["language", "framework", "key-lib"],
  "feasibility_score": 0.70,
  "mode_rationale": "One sentence: why this mode fits this persona."
}
""".strip()


def _build_prompt(
    category: IdeaCategory,
    mode: str,
    persona: str,
    avoid_list: list[str],
    artifact_type: str | None = None,
) -> str:
    avoid_block = "\n".join(avoid_list) if avoid_list else "(none yet)"

    # Two prompt frames: the project-pitch (default — every category before
    # v0.15a used this) and the artifact-shape pitch (Claude Lab categories).
    if artifact_type is None:
        artifact_block = ""
        headline = f"You are pitching a project idea in the {category.value} category."
    else:
        artifact_block = (
            f"## Artifact type: {artifact_type}\n{_ARTIFACT_PROMPTS[artifact_type]}\n\n"
        )
        headline = (
            f"You are pitching a {artifact_type.upper()} for the "
            f"{category.value} category — not a generic project, this "
            f"specific artifact shape."
        )

    return (
        f"{headline}\n\n"
        f"## Persona\n{persona}\n\n"
        f"## Generation mode: {mode}\n{_MODE_PROMPTS[mode]}\n\n"
        f"{artifact_block}"
        f"## Do NOT produce anything resembling these recent ideas\n"
        f"(no renames, no verb-tense variants, no 'X for {{vertical}}' clones):\n"
        f"{avoid_block}\n\n"
        f"## Output\n{_JSON_SCHEMA_INSTRUCTION}\n"
    )


def _strip_codefence(raw: str) -> str:
    raw = raw.strip()
    if "```json" in raw:
        return raw.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in raw:
        return raw.split("```", 1)[1].split("```", 1)[0].strip()
    return raw


def _parse_idea_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_codefence(raw))
    except json.JSONDecodeError:
        return None
    required = ("name", "tagline", "description", "market_analysis", "mvp_scope")
    if not all(isinstance(data.get(k), str) and data[k] for k in required):
        return None
    return data


def _build_idea_from_payload(
    payload: dict[str, Any],
    category: IdeaCategory,
    mode: str,
    artifact_type: str | None = None,
) -> Idea | None:
    try:
        score = float(payload.get("feasibility_score", 0.7))
    except (TypeError, ValueError):
        score = 0.7
    score = max(0.0, min(1.0, score))
    tech = payload.get("tech_stack") or ["python"]
    if not isinstance(tech, list):
        tech = ["python"]
    try:
        return Idea(
            name=payload["name"].strip()[:160],
            tagline=payload["tagline"].strip()[:200],
            description=payload["description"].strip(),
            category=category,
            market_analysis=payload["market_analysis"].strip(),
            feasibility_score=score,
            mvp_scope=payload["mvp_scope"].strip(),
            tech_stack=[str(t)[:40] for t in tech][:8],
            generation_mode=mode,
            artifact_type=artifact_type,
        )
    except Exception:  # pydantic validation errors etc.
        logger.exception("llm_generator: failed to build Idea from payload")
        return None


# --------------------------------------------------------------------------- #
# Top-level entry                                                             #
# --------------------------------------------------------------------------- #


async def generate_idea_llm(
    db: Database,
    category: IdeaCategory,
    *,
    mode: str | None = None,
    artifact_type: str | None = None,
    backend: LLMBackend | None = None,
) -> LLMGenerationResult | None:
    """One LLM-first generation. Returns None when no backend reaches or the
    response fails parsing — caller falls back to the template path.

    `artifact_type` is None for the default project-pitch shape. For the
    Claude Lab categories (CLAUDE_SKILLS_AGENTS, AI_MARKETPLACE) we rotate
    through 8 artifact shapes (skill / sub-agent / mcp-server / hook /
    slash-command / workflow / protocol / ability) — pick happens here if
    the caller doesn't specify.
    """
    backend = backend if backend is not None else resolve_cheap_backend()
    if backend is None:
        return None

    mode = mode if mode in GENERATION_MODES else await pick_least_used_mode(db, category)

    # Artifact rotation: only Claude Lab categories cycle through types.
    if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
        artifact_type = None
    if artifact_type is None and category in _ARTIFACT_ROTATION_CATEGORIES:
        artifact_type = await pick_least_used_artifact(db, category)

    persona = _pick_persona(category)
    avoid = await _recent_idea_lines(db, category)
    prompt = _build_prompt(category, mode, persona, avoid, artifact_type=artifact_type)

    raw = backend.call(prompt) or ""
    if not raw.strip():
        logger.info("llm_generator: backend returned empty response (mode=%s)", mode)
        return None

    payload = _parse_idea_payload(raw)
    if payload is None:
        logger.info("llm_generator: payload parse failed (mode=%s)", mode)
        return None

    idea = _build_idea_from_payload(payload, category, mode, artifact_type=artifact_type)
    if idea is None:
        return None

    return LLMGenerationResult(
        idea=idea,
        mode=mode,
        persona=persona,
        backend=backend.name,
        raw_response=raw,
        artifact_type=artifact_type,
    )
