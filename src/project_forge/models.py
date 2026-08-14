"""Core data models for Project Forge."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class IdeaCategory(StrEnum):
    SECURITY_TOOL = "security-tool"
    MARKET_GAP = "market-gap"
    VULNERABILITY_RESEARCH = "vulnerability-research"
    AUTOMATION = "automation"
    DEVOPS_TOOLING = "devops-tooling"
    PRIVACY = "privacy"
    COMPLIANCE = "compliance"
    OBSERVABILITY = "observability"
    PQC_CRYPTOGRAPHY = "pqc-cryptography"
    NIST_STANDARDS = "nist-standards"
    RFC_SECURITY = "rfc-security"
    CRYPTO_INFRASTRUCTURE = "crypto-infrastructure"
    SELF_IMPROVEMENT = "self-improvement"
    # v0.12 scope expansion beyond IT/security so generation has fresh
    # idea space once the original 13 categories saturate. Adding here
    # lets every downstream stage (CATEGORY_SEEDS, horizontal pair
    # picker, super-idea rotation) pick them up automatically.
    AUTOMATION_INCOME = "automation-income"
    CONSUMER_APP = "consumer-app"
    PRODUCTIVITY = "productivity"
    CREATOR_TOOLS = "creator-tools"
    # v0.15 — frontier-AI / Claude-ecosystem space. Where money-bots ask
    # "how do we make a dollar", these ask "how do we extend Claude's
    # capability ceiling". Different goal, different personas, different
    # scoring axis (ambition_score) — same generation pipeline.
    CLAUDE_SKILLS_AGENTS = "claude-skills-agents"
    AI_MARKETPLACE = "ai-marketplace"
    # v0.16 money-bot expansion — fundable, shippable product shapes that
    # the original content/automation framing missed. These all carry a
    # fundability bonus and surface on /money-bots.
    MICRO_SAAS = "micro-saas"
    VERTICAL_SAAS = "vertical-saas"
    ECOMMERCE_TOOLS = "ecommerce-tools"
    FINTECH_TOOLS = "fintech-tools"
    # v0.16 Claude Lab expansion — the other axes of the agent ecosystem
    # beyond skills/marketplace: the runtime that runs agents, the evals
    # that prove they work, the security that keeps them safe, and the
    # memory that gives them continuity. Ambition-scored, artifact-rotated.
    AGENT_INFRA = "agent-infra"
    CLAUDE_EVALS = "claude-evals"
    AGENT_SECURITY = "agent-security"
    CONTEXT_MEMORY = "context-memory"
    # v0.19 Crypto/Web3 money board — fundable on-chain opportunities. The
    # honest crypto money map: infra, security, DeFi tooling, stablecoin
    # payment rails, and compliance — where the real budgets are — NOT
    # speculative NFT-art minting. Reuses fundability_score; surfaces on
    # /crypto. The operator's PKI/security background is a first-class lens
    # (onchain-security). Kept out of MONEY_CATEGORIES so /crypto is its
    # own board.
    ONCHAIN_SECURITY = "onchain-security"
    WEB3_INFRA = "web3-infra"
    DEFI_TOOLING = "defi-tooling"
    STABLECOIN_PAYMENTS = "stablecoin-payments"
    CRYPTO_COMPLIANCE = "crypto-compliance"
    # v0.20 Cashflow board — folding-cash plays. Where the money categories
    # pitch fundable products, these pitch capital-light systems with the
    # shortest path to actual dollars: productized expertise (the operator's
    # PKI edge), build-once digital assets, lean commerce operations
    # (dropshipping done honestly), lead-gen assets, and data-edge flipping.
    # Ranked by their own axis (cashflow_score), not fundability, whose
    # recurring-SaaS bias mis-ranks exactly these shapes.
    PRODUCTIZED_SERVICES = "productized-services"
    DIGITAL_PRODUCTS = "digital-products"
    COMMERCE_OPS = "commerce-ops"
    LEAD_GENERATION = "lead-generation"
    FLIPPING_ARBITRAGE = "flipping-arbitrage"
    # v0.23 PKI board — the operator's home turf, finally its own board.
    # PKI plumbing broadly: the revocation and sizing problems the PQ
    # transition detonates, plus the classical lifecycle/CA/identity pain
    # that already breaks production today. Deliberately NEW categories
    # rather than reusing PQC_CRYPTOGRAPHY / CRYPTO_INFRASTRUCTURE (which
    # the Sniper board already claims) so /pki stays a disjoint board.
    PKI_REVOCATION = "pki-revocation"
    CERT_LIFECYCLE = "cert-lifecycle"
    PQC_MIGRATION = "pqc-migration"
    CA_OPERATIONS = "ca-operations"
    CERT_IDENTITY = "cert-identity"
    # v0.24 Money Bots rework — the board finally means what it says. These
    # are not product shapes; they are ways to put CAPITAL to work through a
    # venue's API with little or no human intervention. The unit of value is
    # an edge (a mechanism that pays), not a customer. Everything the old
    # money board pitched still exists under PRODUCT_MONEY_CATEGORIES.
    MARKET_MAKING = "market-making"
    INCENTIVE_CAPTURE = "incentive-capture"
    CROSS_VENUE_ARBITRAGE = "cross-venue-arbitrage"
    BASIS_CARRY = "basis-carry"
    CAPITAL_AUTOMATION = "capital-automation"


# --------------------------------------------------------------------------- #
# Themed-dashboard category groupings                                         #
# --------------------------------------------------------------------------- #
# Canonical source of truth for which categories belong to the two themed
# dashboards. Centralized here so the /money-bots and /claude-lab routes,
# the dashboard stats counter, and the auto-promote picker can't drift
# apart — add a category in ONE place and every surface picks it up.

# v0.24 — the /money-bots board. A money bot deploys capital through a
# venue's API and earns from a named mechanism: quoting a book, capturing a
# published incentive, closing a price difference, holding a carry, or
# automating a legal cash-management loop. Scored by bot_edge_score, gated
# on having a real venue and a real API surface.
MONEY_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.MARKET_MAKING,
    IdeaCategory.INCENTIVE_CAPTURE,
    IdeaCategory.CROSS_VENUE_ARBITRAGE,
    IdeaCategory.BASIS_CARRY,
    IdeaCategory.CAPITAL_AUTOMATION,
)

# The product shapes the money board used to hold (v0.12–v0.16). Still
# generated, still fundability-scored, still what Sniper hunts and what the
# promote loop files issues against — they just no longer occupy the board
# named for bots. Reachable on /explore.
PRODUCT_MONEY_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.AUTOMATION_INCOME,
    IdeaCategory.CREATOR_TOOLS,
    IdeaCategory.CONSUMER_APP,
    IdeaCategory.PRODUCTIVITY,
    IdeaCategory.MICRO_SAAS,
    IdeaCategory.VERTICAL_SAAS,
    IdeaCategory.ECOMMERCE_TOOLS,
    IdeaCategory.FINTECH_TOOLS,
)

CLAUDE_LAB_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.CLAUDE_SKILLS_AGENTS,
    IdeaCategory.AI_MARKETPLACE,
    IdeaCategory.AGENT_INFRA,
    IdeaCategory.CLAUDE_EVALS,
    IdeaCategory.AGENT_SECURITY,
    IdeaCategory.CONTEXT_MEMORY,
)

# v0.16 Sniper board — categories the snipe generator hunts incumbents in.
# Spans the commercial money categories PLUS the fat-incumbent IT/security
# space (Venafi, DigiCert, Vault, Okta, CrowdStrike, Datadog, …), which is
# the operator's home turf. The /sniper page itself filters on
# snipe_score IS NOT NULL, so these only bound where churn picks from.
SNIPER_CATEGORIES: tuple["IdeaCategory", ...] = (
    *PRODUCT_MONEY_CATEGORIES,
    IdeaCategory.SECURITY_TOOL,
    IdeaCategory.DEVOPS_TOOLING,
    IdeaCategory.OBSERVABILITY,
    IdeaCategory.COMPLIANCE,
    IdeaCategory.CRYPTO_INFRASTRUCTURE,
    IdeaCategory.PQC_CRYPTOGRAPHY,
)

# v0.19 Crypto/Web3 board — a 4th money board (Money-Bots pattern) that
# surfaces FUNDABLE on-chain ideas, not speculative NFT-art minting.
# Reuses fundability_score, so no schema/scheduler change: these auto-
# generate on the expand rotation and auto-score on the fundability
# back-fill cadence. Disjoint from MONEY_CATEGORIES so /crypto and
# /money-bots stay clean, separate boards.
CRYPTO_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.ONCHAIN_SECURITY,
    IdeaCategory.WEB3_INFRA,
    IdeaCategory.DEFI_TOOLING,
    IdeaCategory.STABLECOIN_PAYMENTS,
    IdeaCategory.CRYPTO_COMPLIANCE,
)

# v0.20 Cashflow board — the folding-cash grouping. Scored by cashflow_score
# (time-to-first-dollar + capital required), a separate axis from
# fundability. Disjoint from every other board so /cashflow stays its own
# clean surface.
CASHFLOW_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.PRODUCTIZED_SERVICES,
    IdeaCategory.DIGITAL_PRODUCTS,
    IdeaCategory.COMMERCE_OPS,
    IdeaCategory.LEAD_GENERATION,
    IdeaCategory.FLIPPING_ARBITRAGE,
)

# v0.23 PKI board — a think tank pointed at certificate plumbing. Ranked by
# its own axis (pki_urgency_score: deadline pressure x blast radius x how
# badly today's tooling fails), because neither fundability nor cashflow
# captures "this breaks in 2030 and nobody has a migration path". Disjoint
# from every other board so /pki is its own clean surface.
PKI_CATEGORIES: tuple["IdeaCategory", ...] = (
    IdeaCategory.PKI_REVOCATION,
    IdeaCategory.CERT_LIFECYCLE,
    IdeaCategory.PQC_MIGRATION,
    IdeaCategory.CA_OPERATIONS,
    IdeaCategory.CERT_IDENTITY,
)


# Categories whose board owns its own ADMISSION GATE. The general generation
# rotation (expand, template auto-scan, super-idea horizontal) must not
# produce into these: anything that arrives outside the gated cadence would
# otherwise land in the category, get back-filled a score, and appear on a
# board that advertises itself as selective.
#
# This is the "back door" fix. The PKI board shipped with a hard gate on its
# own cadence while the ordinary rotation kept generating into the same five
# categories — 28 of the first 77 board items had never seen the gate, and
# 201 more were queued behind the score back-fill. The board query ALSO
# filters on generation_mode, so this constant is the efficiency half (stop
# generating what the board will never show) and the query is the guarantee.
GATED_CATEGORIES: tuple["IdeaCategory", ...] = PKI_CATEGORIES


IdeaStatus = Literal["new", "approved", "scaffolded", "rejected", "archived", "contributed", "implemented"]


class BotVenueFamily(StrEnum):
    """Where the capital is deployed. Drives which probe sources apply and
    which regulatory footnote the board shows."""

    PREDICTION_MARKETS = "prediction-markets"
    CRYPTO_DEFI = "crypto-defi"
    SPORTSBOOK = "sportsbook"
    BROKERAGE = "brokerage"
    OTHER = "other"


class BotSpec(BaseModel):
    """The executable half of a money-bot idea.

    An idea on the money board is only admitted if it can fill this in.
    Every field exists because leaving it out is how a "trading bot idea"
    stays a vibe: without a venue there is nothing to connect to, without
    api_primitives nobody knows if the venue even exposes the operation,
    without a mechanism the returns are magic, and without kill_criteria a
    bot with real money on it has no defined way to stop.
    """

    venue: str
    venue_url: str | None = None
    family: BotVenueFamily = BotVenueFamily.OTHER
    # The concrete API operations the bot needs — the first thing to check
    # against the venue's real docs before writing a line of code.
    api_primitives: list[str] = Field(default_factory=list)
    # Where the money actually comes from. "The spread", "the venue's
    # published reward budget", "the funding payment" — not "AI predictions".
    mechanism: str
    capital_floor_usd: float = Field(ge=0.0)
    capital_target_usd: float = Field(ge=0.0)
    expected_return: str = ""
    # Why this stops working. Every real edge decays; a spec that claims
    # otherwise is the one to distrust.
    edge_decay: str
    kill_criteria: list[str] = Field(default_factory=list)
    validation_plan: list[str] = Field(default_factory=list)
    legality_note: str = ""
    human_touchpoints: str = ""
    # The strongest objection the red-team panel raised that the strategy
    # could not make go away. Published with the strategy on purpose: a bot
    # that admits where it breaks is worth more than one that pretends the
    # objection was never made.
    surviving_objection: str | None = None
    # What the red team concluded. "vetted" survived the panel outright;
    # "objection-stands" survived a rewrite but kept a live objection;
    # "flagged" means the panel would not pass it.
    #
    # v0.24.1: this is a VERDICT, not a delete. The first cut discarded
    # everything the panel knocked down, so the board sat empty and the
    # operator could not see what the engine had tried or why it failed —
    # which is the most useful thing on the page. Only the legality veto and
    # a missing spec now block storage.
    panel_verdict: str | None = None

    @field_validator("venue", "mechanism", "edge_decay")
    @classmethod
    def _required_text(cls, v: str) -> str:
        stripped = (v or "").strip()
        if not stripped:
            raise ValueError("field cannot be empty — a bot spec without it is not actionable")
        return stripped

    @field_validator("api_primitives")
    @classmethod
    def _needs_api_surface(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip() for p in v if p and p.strip()]
        if not cleaned:
            raise ValueError("at least one API primitive — a strategy with no API surface is a wish")
        return cleaned

    @field_validator("kill_criteria")
    @classmethod
    def _needs_kill_switch(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k and k.strip()]
        if not cleaned:
            raise ValueError("at least one kill criterion — real capital needs a defined stop")
        return cleaned

    @field_validator("capital_target_usd")
    @classmethod
    def _target_at_least_floor(cls, v: float, info) -> float:
        floor = info.data.get("capital_floor_usd")
        if floor is not None and v < floor:
            raise ValueError(f"capital_target_usd ({v}) below capital_floor_usd ({floor})")
        return v


class Idea(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    name: str
    tagline: str
    description: str
    category: IdeaCategory
    market_analysis: str
    feasibility_score: float = Field(ge=0.0, le=1.0)
    mvp_scope: str
    tech_stack: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: IdeaStatus = "new"
    github_issue_url: str | None = None
    project_repo_url: str | None = None
    content_hash: str | None = None
    source_url: str | None = None
    # v0.13 — which `llm_generator` mode produced this idea (or None for
    # template-generated ones). Used by `pick_least_used_mode` so the
    # rotation hits under-represented modes first.
    generation_mode: str | None = None
    # v0.13 fundability scoring — how monetizable does the engine think
    # this idea is. Distinct from feasibility (can we build it). Range
    # 0.0–1.0, None for ideas predating the scorer.
    fundability_score: float | None = None
    # v0.14 — the auto-promote loop flips status to 'approved' and stamps
    # this timestamp + the issue URL so the picker can skip already-promoted
    # ideas idempotently.
    auto_promoted_at: datetime | None = None
    # v0.15 — frontier scoring. fundability asks "can we sell it"; ambition
    # asks "does it push Claude's capability ceiling". Used to sort the
    # /claude-lab page.
    ambition_score: float | None = None
    # v0.15a — which SHAPE of artifact this idea pitches: skill /
    # sub-agent / mcp-server / hook / slash-command / workflow /
    # protocol / ability. None = the default project-pitch shape
    # (everything pre-v0.15a). Only Claude Lab categories rotate
    # through these; money-bot and IT/security categories keep the
    # project-pitch shape. NOTE: Sniper-board ideas reuse this column to
    # store their snipe ANGLE (price-snipe / unbundle / …) — disjoint
    # vocabulary, same column, no collision since snipe categories never
    # artifact-rotate.
    artifact_type: str | None = None
    # v0.16 Sniper board — competitive-displacement axis. Where fundability
    # asks "can we sell it" and ambition "does it push the ceiling",
    # snipe_score asks "can we wedge into a market-PROVEN incumbent's
    # demand". target_incumbent is the named real comp the wedge targets
    # (powers the "vs. X" badge). Both None for non-snipe ideas.
    snipe_score: float | None = None
    target_incumbent: str | None = None
    # v0.18 Missions (#84) — which operator directive this idea was
    # generated against. None for everything the engine dreamt up on its
    # own rotation. Powers the per-mission grids on /missions.
    mission_id: str | None = None
    # v0.20 Cashflow board (#96) — how fast this idea turns into actual
    # dollars, with how little capital. fundability asks "can we sell it";
    # cashflow asks "how soon is the first invoice". Sorted DESC on
    # /cashflow; None for ideas outside the board (or predating the axis).
    cashflow_score: float | None = None
    # v0.23 PKI board — urgency, not money. Deadline pressure x blast radius
    # x how badly today's tooling fails. Doubles as the board's ADMISSION
    # gate, not just its sort order: the hourly probe discards anything that
    # scores under PKI_ADMIT_THRESHOLD, so the board stays a short list of
    # things that actually matter instead of a pile of plausible cert tools.
    pki_urgency_score: float | None = None
    # The concrete artifact this idea is pinned to — an RFC/draft name, a
    # CA/B Forum ballot, a tracker issue, a compliance deadline. Required
    # for admission to /pki: no anchor means it's a vibe, not a finding.
    pki_anchor: str | None = None
    # The strongest counterargument the red-team panel raised that the
    # revision could not make go away. Surfaced on the card on purpose: a
    # finding that admits what is wrong with it is worth more to an engineer
    # than one that pretends the objection was never made.
    pki_objection: str | None = None
    # v0.24 Money Bots — the capital-deployment axis. fundability asks "can
    # we sell it"; bot_edge asks "does this edge survive fees, competition
    # and capacity, and can a bot run it unattended". Doubles as the board's
    # admission gate, like pki_urgency_score.
    bot_edge_score: float | None = None
    # The strategy itself. None for every idea that is not a money bot.
    bot_spec: BotSpec | None = None


MissionStatus = Literal["active", "paused", "archived"]


class _MissionFields(BaseModel):
    """Validated fields shared by Mission and MissionCreateRequest, so the
    API boundary and the persisted model can't drift on what a legal
    directive looks like."""

    title: str
    brief: str
    urls: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_bounds(cls, v: str) -> str:
        stripped = (v or "").strip()
        if not stripped:
            raise ValueError("title cannot be empty")
        if len(stripped) > 120:
            raise ValueError(f"title too long ({len(stripped)} chars; max 120)")
        return stripped

    @field_validator("brief")
    @classmethod
    def _brief_bounds(cls, v: str) -> str:
        stripped = (v or "").strip()
        if len(stripped) < 10:
            raise ValueError("brief must be at least 10 characters — say what actually matters")
        if len(stripped) > 4000:
            raise ValueError(f"brief too long ({len(stripped)} chars; max 4000)")
        return stripped

    @field_validator("urls")
    @classmethod
    def _urls_valid(cls, v: list[str]) -> list[str]:
        if len(v) > 3:
            raise ValueError(f"at most 3 grounding URLs ({len(v)} given)")
        for url in v:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError(f"Invalid URL: {url!r} — must be http(s)")
        return v


class Mission(_MissionFields):
    """v0.18 (#84) — an operator directive the think tank generates against.

    Where every other generation path picks its own target (category
    rotation, incumbent seeds, live signals), a Mission is the human
    pointing: 'ideas in THIS problem space matter to me'. The brief plus
    fetched URL excerpts ride the `seed` anchor in `generate_idea_llm`.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    category: IdeaCategory | None = None
    status: MissionStatus = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Watermark for the mission cadence — advanced on every generation
    # attempt (saved OR dedup-rejected) so reloads/rejection streaks don't
    # hammer the backend. NULL = never generated against, fires first.
    last_generated_at: datetime | None = None


class MissionCreateRequest(_MissionFields):
    """POST /api/missions body. Category arrives as a plain string from the
    dashboard select; validated against IdeaCategory here so the route 422s
    instead of 500ing on garbage."""

    category: str | None = None

    @field_validator("category")
    @classmethod
    def _category_known(cls, v: str | None) -> str | None:
        if not v:
            return None
        IdeaCategory(v)  # raises ValueError on unknown values
        return v


class Resource(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    domain: str
    name: str
    description: str
    url: str | None = None
    categories: list[str] = Field(default_factory=list)
    idea_count: int = 0
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UrlIngestRequest(BaseModel):
    url: str
    category: str | None = None
    notes: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_format(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"Invalid URL: {v!r} — must be http(s)")
        return v


class TextIngestRequest(BaseModel):
    """Free-form fragment the user wants expanded into an Idea.

    Companion to UrlIngestRequest. The text can be a half-formed thought,
    a research question, a frustration, a code snippet — anything Sonnet
    can structure into a project pitch.
    """

    text: str
    category: str | None = None

    @field_validator("text")
    @classmethod
    def validate_text_not_empty(cls, v: str) -> str:
        stripped = (v or "").strip()
        if not stripped:
            raise ValueError("text cannot be empty or whitespace-only")
        if len(stripped) > 10000:
            raise ValueError(f"text too long ({len(stripped)} chars; max 10000)")
        return stripped


class Challenge(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    idea_id: str
    question: str = Field(min_length=1)
    challenge_type: str = "freeform"
    focus_area: str = "all"
    tone: str = "skeptical"
    response: str = ""
    verdict: str = "no_change"
    confidence: float = 0.5
    changes: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    applied_at: datetime | None = None  # set when apply-changes endpoint runs


class FilteredIdea(BaseModel):
    """Audit trail for ideas blocked by dedup or quality review."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    idea_name: str
    idea_tagline: str
    idea_category: IdeaCategory
    filter_reason: str  # e.g. "duplicate:content_hash", "duplicate:tagline_similarity:0.85", "quality:buzzwords"
    original_idea_json: str
    filtered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    similar_to_id: str | None = None


AuditStatus = Literal["implemented", "partial", "not_implemented", "unknown"]


class PromotedIdeaAudit(BaseModel):
    """Audit result for a promoted/approved idea — tracks implementation evidence."""

    idea_id: str
    idea_name: str
    status: AuditStatus
    evidence: list[str] = Field(default_factory=list)
    github_issue_number: int | None = None
    github_issue_state: str | None = None
    recommendation: str | None = None


class ScaffoldSpec(BaseModel):
    idea_id: str
    repo_name: str
    language: Literal["python", "node", "rust", "go"]
    framework: str | None = None
    features: list[str] = Field(default_factory=lambda: ["ci", "tests", "readme"])
    initial_issues: list[dict] = Field(default_factory=list)


class IdeaDenial(BaseModel):
    """Audit trail for idea denial with reasoning."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    idea_id: str
    reason: str = Field(min_length=1)
    denied_by: str | None = None
    denied_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


RoundStatus = Literal["pending", "in_progress", "completed"]


class SelectionRound(BaseModel):
    """A round of head-to-head idea selection and comparison."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    round_number: int = Field(ge=1)
    idea_ids: list[str] = Field(min_length=2)
    status: RoundStatus = "pending"
    results: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class GenerationRun(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    category: IdeaCategory
    idea_id: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    success: bool = False
    error: str | None = None


class RepoEntry(BaseModel):
    """A GitHub repository in the portfolio registry."""

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    repo_full_name: str  # "owner/repo-name"
    description: str
    topics: list[str] = Field(default_factory=list)
    last_synced: str = ""
