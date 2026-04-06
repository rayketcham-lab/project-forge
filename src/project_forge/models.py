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
