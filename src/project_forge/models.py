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


# --------------------------------------------------------------------------- #
# Themed-dashboard category groupings                                         #
# --------------------------------------------------------------------------- #
# Canonical source of truth for which categories belong to the two themed
# dashboards. Centralized here so the /money-bots and /claude-lab routes,
# the dashboard stats counter, and the auto-promote picker can't drift
# apart — add a category in ONE place and every surface picks it up.

MONEY_CATEGORIES: tuple["IdeaCategory", ...] = (
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
    *MONEY_CATEGORIES,
    IdeaCategory.SECURITY_TOOL,
    IdeaCategory.DEVOPS_TOOLING,
    IdeaCategory.OBSERVABILITY,
    IdeaCategory.COMPLIANCE,
    IdeaCategory.CRYPTO_INFRASTRUCTURE,
    IdeaCategory.PQC_CRYPTOGRAPHY,
)


IdeaStatus = Literal["new", "approved", "scaffolded", "rejected", "archived", "contributed", "implemented"]


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
